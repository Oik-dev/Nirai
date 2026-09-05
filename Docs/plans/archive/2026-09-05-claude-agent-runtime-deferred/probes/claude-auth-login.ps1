python -c "import pathlib, subprocess, claude_agent_sdk, sys; p=pathlib.Path(claude_agent_sdk.__file__).parent/'_bundled'/'claude.exe'; sys.exit(subprocess.call([str(p),'auth','login']))"
exit $LASTEXITCODE
