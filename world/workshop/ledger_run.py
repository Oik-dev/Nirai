"""Verify recorded failures and nonidentical catalog clips using one physical gate."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from body_check import LIMITS, evaluate

HERE = Path(__file__).resolve().parent
WORLD = HERE.parent
CATALOG = WORLD / "window/assets/motions"
CASES = HERE / "counterexamples"

def checksum(path):
    return hashlib.sha256(path.read_bytes()).digest()

def render(outdir, names, historical):
    outdir.mkdir(parents=True, exist_ok=True)
    # Always resample: a previous JSON cannot prove that VRMA, model and
    # sampler still match it after code or avatar changes.
    missing = list(names)
    if missing:
        cmd = ["node", str(HERE/"look.mjs"), str(outdir)]
        subprocess.run(cmd + (["--counterexamples"] if historical else []) + missing,
                       cwd=WORLD, check=True)

def run(output):
    ledger = json.loads((CASES/"ledger.json").read_text(encoding="utf-8"))
    examples = [entry["file"] for entry in ledger]
    catalog = [p.name for p in sorted(CATALOG.glob("*.vrma"))]
    # Never trust an old JSON after either VRMA, avatar, or sampler changes.
    # User-specific samples remain local to the work area, not Git.
    render(output/"examples", examples, True)
    render(output/"catalog", catalog, False)
    output_rows = {"examples": {}, "catalog": {}, "limits": LIMITS}
    entry_by_name = {r["file"]:r for r in ledger}
    for label, files in (("examples", examples), ("catalog", catalog)):
        for filename in files:
            source = CASES/filename if label=="examples" else CATALOG/filename
            if label=="catalog" and any(checksum(source)==checksum(CASES/e) for e in examples):
                output_rows[label][filename] = {"excluded": "identical to historical counterexample"}
                continue
            samplefile = output/label/(Path(filename).stem+".samples.json")
            samples=json.loads(samplefile.read_text(encoding="utf-8"))
            result=evaluate(samples)
            output_rows[label][filename]={
                "pass":result["pass"],"penetration":result["penetration"],
                "support":result["support"],"alignment":result["alignment"],
                "fallback_radii":result["fallback_radii"],
            }
            print(label, filename, "collision(m)",result["penetration"]["depth_m"],
                  "support residual",result["support"]["root_residual"],
                  "effort(m)",result["support"]["effort_m"],
                  "contact frames",result["support"]["observations"],flush=True)
    collision_good=output_rows["catalog"]["伸び.vrma"]["penetration"]["depth_m"]
    swim=output_rows["examples"]["2026-10-15-泳ぐ.vrma"]["penetration"]["depth_m"]
    interval={"accepted":collision_good,"rejected":swim,"threshold":LIMITS["penetration"]["max"],"unit":"m"}
    all_positive=all(
        not output_rows["examples"][entry["file"]][entry["broken"]]["pass"] for entry in ledger)
    all_catalog=all(r.get("pass", True) for r in output_rows["catalog"].values())
    valid_interval=(collision_good <= LIMITS["penetration"]["max"] < swim)
    midpoint = round((collision_good + swim) / 2, 6)
    valid_interval = valid_interval and abs(midpoint - LIMITS["penetration"]["max"]) <= 0.000001
    output_rows["validation"]={"historical_fail_in_reported_property":all_positive,
                                "nonidentical_catalog_pass":all_catalog,
                                "penetration_interval":interval,
                                "penetration_threshold_in_interval":valid_interval}
    (output/"ledger-results.json").write_text(json.dumps(output_rows,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("VALIDATION",json.dumps(output_rows["validation"],ensure_ascii=False),flush=True)
    return all_positive and all_catalog and valid_interval

if __name__=="__main__":
    if len(sys.argv)!=2: raise SystemExit("Usage: ledger_run.py <untracked-check-directory>")
    if not run(Path(sys.argv[1])): raise SystemExit(2)
