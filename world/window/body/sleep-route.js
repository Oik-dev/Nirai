import { HOME_ACTIVITY } from './activities.js';
import { LANDING_SPEED, LANDING_FADE_TO_SIT } from './landing.js';
import { placeAt } from './place.js';
import { restAt } from './rest.js';

const HOME = Object.freeze({ name: HOME_ACTIVITY, since: null, from: null });
const SAND = Object.freeze({ name: '砂地で休む', since: null, from: null });
const iso = at => new Date(at).toISOString();

// A window records changes as {at, asleep, activity?}; activity is the choice
// at the moment of falling asleep. The first observation is at=0. No runtime
// animation state is needed, including after a reload.
//
// The sand leg is an ordinary placeAt chain: interrupting swimming midway
// starts at the current position, and waking returns via that same chain.
export function sleepRouteAt(history, chosenActivity, now, view, reclineSeconds, sitEntrySeconds) {
  if (!Array.isArray(history) || !history.length || history[0].at !== 0
    || typeof history[0].asleep !== 'boolean' || !Number.isFinite(now)
    || !(reclineSeconds > 0) || !(sitEntrySeconds >= 0)) return null;

  const chosen = chosenActivity ?? HOME;
  const enriched = [];
  let sand = null;
  let seatedAt = 0;
  let releaseAt = null;
  let previousAt = -1;
  for (const event of history) {
    if (!Number.isFinite(event.at) || event.at < previousAt || typeof event.asleep !== 'boolean') return null;
    previousAt = event.at;
    if (event.at > now) break;
    if (event.asleep) {
      if (!sand || releaseAt !== null && event.at >= releaseAt) {
        const origin = event.activity ?? chosen;
        // Already seated on the sand, including a window opened mid-sleep.
        const stationary = event.at === 0 || (origin.name === SAND.name
          && (!origin.from || placeAt(origin, event.at, view).arrivedAt !== null));
        sand = stationary ? SAND : { name: SAND.name, since: iso(event.at), from: origin };
        if (stationary) seatedAt = event.at;
        else {
          // Travel is capped at 30 s. A time beyond that resolves arrival.
          const arrival = placeAt(sand, event.at + 60_000, view).arrivedAt;
          if (!Number.isFinite(arrival)) return null;
          seatedAt = arrival + (sitEntrySeconds / LANDING_SPEED + LANDING_FADE_TO_SIT) * 1000;
        }
      }
      enriched.push({ asleep: true, at: event.at, seatedAt: Math.max(seatedAt, event.at) });
      releaseAt = null;
    } else {
      if (sand && (enriched.length === 0 || enriched.at(-1).asleep)) {
        const atWake = restAt(enriched, event.at, 0, reclineSeconds);
        if (!atWake) return null;
        releaseAt = event.at + atWake.level * reclineSeconds * 1000;
      }
      enriched.push({ asleep: false, at: event.at });
    }
  }

  if (!enriched.length) return null;
  const rest = restAt(enriched, now, 0, reclineSeconds);
  if (!rest) return null;
  if (!sand) return { activity: chosen, rest, sand: null, seatedAt: null, releaseAt: null };
  if (rest.asleep || releaseAt === null || now < releaseAt) {
    return { activity: sand, rest, sand, seatedAt, releaseAt };
  }
  // Activity choices made after getting up already carry their own time and
  // origin. Never replace those with the old route out of the sand.
  const chosenSince = chosen.since ? Date.parse(chosen.since) : NaN;
  if (Number.isFinite(chosenSince) && chosenSince > releaseAt) {
    return { activity: chosen, rest, sand, seatedAt, releaseAt };
  }
  if (chosen.name === SAND.name) {
    return { activity: sand, rest, sand, seatedAt, releaseAt };
  }
  return {
    activity: { name: chosen.name, since: iso(releaseAt), from: sand },
    rest, sand, seatedAt, releaseAt,
  };
}
