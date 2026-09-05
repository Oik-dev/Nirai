python -c "import pathlib, subprocess, json, claude_agent_sdk; p=pathlib.Path(claude_agent_sdk.__file__).parent/'_bundled'/'claude.exe'; r=subprocess.run([str(p),'auth','status'],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=20); print(json.dumps({'code':r.returncode,'stdout':r.stdout,'stderr':r.stderr}, ensure_ascii=True))"
exit $LASTEXITCODE
