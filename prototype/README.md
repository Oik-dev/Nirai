# Nirai v2 UI Prototype

Standalone HTML/CSS/JS mock. No install or build required.

## Open

Double-click:

`D:\Products\Nirai\prototype\index.html`

## Current interactions

- Dashboard expand / collapse
- Collapsed summary: RUN / CHECK / PAUSE / COMPLETE
- Resident card click selects the Resident for the next new Chat / Task
- `＋` immediately creates a new Task for the selected Resident
- First Chat message starts the Task and shows its first Activity
- Task selection also selects its Chat
- Pause / Resume from Chat header
- Task discard from the expanded Task footer with confirmation
- Completed / Cancelled Tasks move to Archive automatically
- TASK / ARCHIVE accordion switching with internal scrolling
- Resident status and Limit gauges
- Landscape: TASK left / CHAT right
- Portrait: TASK top / CHAT bottom

## Terminology

- Task = Masterが依頼する仕事
- Activity = Task内の現在または最近の仕事を人間向けに見せる表示
- Run = BackendでTaskがCapabilityを1回利用する実行記録。PrototypeはRunの詳細状態を正本として持たない

## Scope

This prototype is only for UI structure and interaction decisions. It does not implement the Nirai v2 Control Plane.
