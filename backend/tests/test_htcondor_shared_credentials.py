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


if __name__ == "__main__":
    unittest.main()
