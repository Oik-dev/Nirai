import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {cardanDegrees} from './joint-angles.ts';
const definitions = JSON.parse(readFileSync(new URL('./joint-ranges.json',import.meta.url),'utf8')).joints;
const vectors = JSON.parse(readFileSync(new URL('./joint-angles.fixtures.json',import.meta.url),'utf8')) as
  {bone:string,quaternion:number[],angles:number[]}[];
test('Python SciPy and TypeScript calculate identical intrinsic signed Cardan angles', () => {
  for (const row of vectors) {
    const result = cardanDegrees(row.quaternion,definitions[row.bone]);
    for (let i=0;i<3;i++) assert.ok(Math.abs(result[i]-row.angles[i])<.00002,
      row.bone+' '+i+': got '+result[i]+' expected '+row.angles[i]);
  }
});
