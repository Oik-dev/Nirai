// Run the gate (world/sea/gate.ts; GATE=<file URL> to try another copy) over sampled clips and summarise the issues per clip.
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
const { checkMotion } = await import(new URL(process.env.GATE ?? '../sea/gate.ts', import.meta.url));
for (const dir of process.argv.slice(2)) {
  for (const file of readdirSync(dir).filter(f => f.endsWith('.samples.json'))) {
    const result = checkMotion(JSON.parse(readFileSync(join(dir, file), 'utf8')));
    const summary = {};
    for (const x of result.issues) {
      const key = `${x.kind}/${x.metric}`;
      const s = (summary[key] ??= { ranges: 0, frames: 0, worst: 0, bones: new Set(), limit: x.limit, unit: x.unit });
      s.ranges++; s.frames += x.toFrame - x.fromFrame + 1; s.worst = Math.max(s.worst, Math.abs(x.value ?? 0)); s.bones.add(x.bone);
    }
    console.log(`${file.replace('.samples.json', '').padEnd(16)} pass=${result.pass}`);
    for (const [k, s] of Object.entries(summary))
      console.log(`   ${k.padEnd(28)} frames=${String(s.frames).padStart(4)} worst=${s.worst.toFixed(3)} limit=${s.limit}${s.unit} bones=${[...s.bones].join(',')}`);
  }
}
