from pathlib import Path
import sys

backend = Path("D:/local_web_module_system/backend")
project_root = Path("D:/local_web_module_system")

sys.path.insert(0, str(backend))

from app.htcondor_cluster_manager import HTCondorClusterManager

manager = HTCondorClusterManager(
    base_dir=backend,
    project_root=project_root,
)

for cid in ["30", "31"]:
    print("=" * 80)
    print("CANCEL", cid)
    result = manager.cancel_job(cluster_id=cid)
    for k, v in result.items():
        print(f"{k}: {v}")
