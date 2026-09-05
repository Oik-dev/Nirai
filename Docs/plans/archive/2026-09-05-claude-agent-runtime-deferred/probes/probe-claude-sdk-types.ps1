python -c "import inspect,json; from claude_agent_sdk.types import PermissionRuleValue,PermissionUpdate; print(json.dumps({'rule':str(inspect.signature(PermissionRuleValue)),'update':str(inspect.signature(PermissionUpdate))},ensure_ascii=False))"
exit $LASTEXITCODE
