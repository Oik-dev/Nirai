python -c "import inspect,json; import claude_agent_sdk._internal.transport.subprocess_cli as sp; print(json.dumps({'sig':str(inspect.signature(sp.SubprocessCLITransport)),'init':inspect.getsource(sp.SubprocessCLITransport.__init__)}, ensure_ascii=True))"
exit $LASTEXITCODE
