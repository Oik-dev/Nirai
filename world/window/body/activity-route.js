// A recorded visit has a scheduled end. Resolve the whole route from timestamps,
// so returning from the window never depends on a timer or an SSE notification.
export function activityAt(activity, now) {
  if (!activity?.route) return activity;
  const { origin, changes } = activity.route;
  let current = { ...origin, from: null };
  for (const change of changes) {
    if (Date.parse(change.since) > now) break;
    current = { name: change.name, since: change.since, from: current };
  }
  return current;
}
