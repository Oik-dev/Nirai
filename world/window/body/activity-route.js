// A recorded visit has a scheduled end. Resolve the whole route from timestamps,
// so returning from the window never depends on a timer or an SSE notification.
import { MAX_TRAVEL_MS } from './place.js';

export function activityAt(activity, now) {
  if (!activity?.route) return activity;
  const { origin, changes } = activity.route;
  let current = { ...origin, from: null };
  for (const change of changes) {
    const at = Date.parse(change.since);
    if (at > now) break;
    // 移動が完了した活動の過去の道筋は、次の移動の出発点には不要。
    if (at - Date.parse(current.since) >= MAX_TRAVEL_MS) current = { ...current, from: null };
    current = { name: change.name, since: change.since, from: current };
  }
  return current;
}
