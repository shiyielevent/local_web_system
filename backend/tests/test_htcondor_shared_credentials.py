import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.htcondor_cluster_manager import HTCondorClusterError, HTCondorClusterManager


class SharedDirectoryCredentialTests(unittest.TestCase):
    def make_manager(self, root: Path) -> HTCondorClusterManager:
        manager = HTCondorClusterManager(root / "backend", root)
        manager._local_ipv4_list = lambda: ["192.168.2.145"]
        return manager

    def test_local_parent_share_uses_automatic_submit_account(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {
                "COMPUTERNAME": "PARENT-PC",
                "LOCAL_WEB_HTCONDOR_SHARE_USER": "OLD-PC\\Administrator",
                "LOCAL_WEB_HTCONDOR_SHARE_PASSWORD": "old-password",
            },
            clear=False,
        ):
            manager = self.make_manager(Path(temp_dir))
            manager._read_submit_account_password = lambda: "automatic-password"

            user, password, source = manager._shared_directory_job_credentials(
                {"unc_root": r"\\192.168.2.145\data"}
            )

            self.assertEqual(user, r"PARENT-PC\LocalWebCondor")
            self.assertEqual(password, "automatic-password")
            self.assertEqual(source, "automatic_parent_localwebcondor")

    def test_child_join_without_parent_password_defers_smb_to_remote_job(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {
                "LOCAL_WEB_HTCONDOR_SHARE_USER": "",
                "LOCAL_WEB_HTCONDOR_SHARE_PASSWORD": "",
            },
            clear=False,
        ):
            manager = self.make_manager(Path(temp_dir))
            manager._run = lambda *args, **kwargs: self.fail(
                "child join must not attempt an unauthenticated SMB connection"
            )

            result = manager.connect_parent_shared_io(
                parent_ip="10.198.130.4",
                share_name="H8Data",
                unc_root=r"\\10.198.130.4\H8Data",
            )
            config = manager.shared_io_config()

            self.assertTrue(result["ok"])
            self.assertTrue(result["runtime_managed"])
            self.assertEqual(result["commands"], [])
            self.assertEqual(result["unc_root"], "")
            self.assertTrue(config["enabled"])
            self.assertTrue(config["runtime_managed"])
            self.assertEqual(config["share_name"], "")
            self.assertIn("无需在子节点保存父节点 Windows 密码", config["connect_message"])

    def test_child_join_with_stale_credentials_falls_back_to_runtime_connection(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {
                "LOCAL_WEB_HTCONDOR_SHARE_USER": r"OLD-PARENT\Administrator",
                "LOCAL_WEB_HTCONDOR_SHARE_PASSWORD": "wrong-password",
            },
            clear=False,
        ):
            root = Path(temp_dir)
            manager = self.make_manager(root)
            manager._run = lambda *args, **kwargs: {
                "return_code": 2,
                "stdout": "",
                "stderr": "logon failure",
            }
            blocked_path = root / "not-a-directory"
            blocked_path.write_text("blocked", encoding="utf-8")

            result = manager.connect_parent_shared_io(
                parent_ip="10.198.130.4",
                share_name="H8Data",
                unc_root=str(blocked_path),
            )
            config = manager.shared_io_config()

            self.assertTrue(result["ok"])
            self.assertTrue(result["runtime_managed"])
            self.assertFalse(result["direct_connect_ok"])
            self.assertEqual(result["unc_root"], "")
            self.assertTrue(config["enabled"])
            self.assertTrue(config["runtime_managed"])
            self.assertIn("已自动切换", config["connect_message"])

    def test_local_parent_share_can_fall_back_to_configured_credentials(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {
                "COMPUTERNAME": "PARENT-PC",
                "LOCAL_WEB_HTCONDOR_SHARE_USER": r"PARENT-PC\ShareUser",
                "LOCAL_WEB_HTCONDOR_SHARE_PASSWORD": "fallback-password",
            },
            clear=False,
        ):
            manager = self.make_manager(Path(temp_dir))

            def fail_to_read_secret():
                raise HTCondorClusterError("secret unavailable")

            manager._read_submit_account_password = fail_to_read_secret
            user, password, source = manager._shared_directory_job_credentials(
                {"unc_root": r"\\192.168.2.145\data"}
            )

            self.assertEqual(user, r"PARENT-PC\ShareUser")
            self.assertEqual(password, "fallback-password")
            self.assertEqual(source, "environment_fallback")

    def test_external_share_keeps_explicit_environment_credentials(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {
                "COMPUTERNAME": "PARENT-PC",
                "LOCAL_WEB_HTCONDOR_SHARE_USER": r"FILE-SERVER\ShareUser",
                "LOCAL_WEB_HTCONDOR_SHARE_PASSWORD": "external-password",
            },
            clear=False,
        ):
            manager = self.make_manager(Path(temp_dir))
            manager._read_submit_account_password = lambda: self.fail(
                "external shares must not read the parent submit-account secret"
            )

            user, password, source = manager._shared_directory_job_credentials(
                {"unc_root": r"\\192.168.2.200\data"}
            )

            self.assertEqual(user, r"FILE-SERVER\ShareUser")
            self.assertEqual(password, "external-password")
            self.assertEqual(source, "environment_external_share")

    def test_remote_job_script_uses_current_parent_automatic_credentials(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {
                "COMPUTERNAME": "PARENT-PC",
                "LOCAL_WEB_HTCONDOR_SHARE_USER": "OLD-PC\\Administrator",
                "LOCAL_WEB_HTCONDOR_SHARE_PASSWORD": "old-password",
            },
            clear=False,
        ):
            root = Path(temp_dir)
            manager = self.make_manager(root)
            manager._read_submit_account_password = lambda: "automatic-password"
            manager.state["shared_io_config"] = {
                "enabled": True,
                "local_root": str(root / "shared-data"),
                "unc_root": r"\\192.168.2.145\data",
                "share_name": "data",
                "role": "parent",
                "connect_ok": True,
            }

            files = manager._write_job_files(
                "job-1",
                ["cmd.exe", "/D", "/C", "echo ok"],
                str(root),
                {},
                target_machine="CHILD-PC",
            )
            run_script = files["run_cmd"].read_text(encoding="utf-8-sig")

            self.assertIn(
                "[HTCONDOR] shared credential source=automatic_parent_localwebcondor",
                run_script,
            )
            self.assertIn(r'/user:"PARENT-PC\LocalWebCondor"', run_script)
            self.assertIn('/pass:"automatic-password"', run_script)
            self.assertNotIn("OLD-PC", run_script)
            self.assertNotIn("old-password", run_script)

    def test_remote_stream_staging_keeps_only_current_and_next_input_batches(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {"COMPUTERNAME": "PARENT-PC"},
            clear=False,
        ):
            root = Path(temp_dir)
            manager = self.make_manager(root)
            manager._read_submit_account_password = lambda: "automatic-password"
            manager.state["shared_io_config"] = {
                "enabled": True,
                "local_root": str(root / "shared-data"),
                "unc_root": r"\\192.168.2.145\data",
                "share_name": "data",
                "role": "parent",
                "connect_ok": True,
            }

            part_dir = root / "shared-data" / "part_1"
            input_dir = part_dir / "fy4_path"
            input_dir.mkdir(parents=True)
            config_path = part_dir / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "fy4_path": "__LOCAL_WEB_JOB_DIR__/stage_current",
                        "out_path": "__LOCAL_WEB_JOB_DIR__/out_path",
                    }
                ),
                encoding="utf-8",
            )
            plan = {
                "version": 1,
                "input_key": "fy4_path",
                "source_dir": r"\\192.168.2.145\data\part_1\fy4_path",
                "direct_input_dir": r"\\192.168.2.145\data\part_1\fy4_path_direct",
                "files": ["a.hdf", "b.hdf", "c.hdf"],
                "batch_size": 2,
                "first_batch_size": 1,
                "output_dirs": ["out_path"],
            }
            stage_env = {
                "LOCAL_WEB_HTCONDOR_STREAM_STAGE_PLAN_B64": base64.b64encode(
                    json.dumps(plan).encode("utf-8")
                ).decode("ascii")
            }

            files = manager._write_job_files(
                "stage-job",
                [r"C:\module\algorithm.exe", str(config_path)],
                r"C:\module",
                stage_env,
                target_machine="CHILD-PC",
            )
            job_dir = files["job_dir"]
            run_script = files["run_cmd"].read_text(encoding="ascii")
            submit_text = files["sub_file"].read_text(encoding="ascii")
            runner_text = (job_dir / "stream_stage_runner.ps1").read_text(encoding="utf-8-sig")
            copied_plan = json.loads((job_dir / "stream_stage_plan.json").read_text(encoding="utf-8"))

            self.assertIn("stream_stage_runner.ps1", submit_text)
            self.assertIn("stream_stage_copy.ps1", submit_text)
            self.assertIn("transfer_output_files = result.txt, out_path", submit_text)
            self.assertNotIn("LOCAL_WEB_HTCONDOR_STREAM_STAGE_PLAN_B64=", run_script)
            self.assertIn("double-buffer input staging enabled", run_script)
            self.assertIn("prefetching next batch", runner_text)
            self.assertIn("running first batch directly from shared directory", runner_text)
            self.assertIn("$remainingFiles = [Math]::Max(0, $files.Count - $firstBatchSize)", runner_text)
            self.assertIn("deleted completed local batch input", runner_text)
            self.assertIn("$batchIndex -gt 0", runner_text)
            self.assertNotIn("Remove-Item -LiteralPath $directInputDir", runner_text)
            self.assertEqual(copied_plan["batch_size"], 2)
            self.assertEqual(copied_plan["first_batch_size"], 1)
            self.assertEqual(copied_plan["direct_input_dir"], r"\\192.168.2.145\data\part_1\fy4_path_direct")
            self.assertEqual(copied_plan["files"], ["a.hdf", "b.hdf", "c.hdf"])
            self.assertTrue((job_dir / "out_path").is_dir())
            self.assertFalse((job_dir / "fy4_path").exists())


if __name__ == "__main__":
    unittest.main()
