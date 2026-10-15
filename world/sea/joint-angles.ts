/** Cardan coordinates in the VRM 1.0 normalized T-pose basis.
 *  The THREE/MuJoCo convention is intrinsic bend -> offAxis -> twist.
 *  A signed axis reverses the component coordinate, never the rotation itself.
 */
export type Axis = 'x' | 'y' | 'z' | '-x' | '-y' | '-z';
export type JointDefinition = { bend: {axis:Axis}; offAxis:{axis:Axis}; twist:{axis:Axis} };
const INDEX: Record<string, number> = { x: 0, y: 1, z: 2 };
export function cardanDegrees(q: readonly number[], def: JointDefinition): [number,number,number] {
  const axes = [def.bend.axis, def.offAxis.axis, def.twist.axis];
  const indices = axes.map(a => INDEX[a.at(-1)!]);
  const [i,j,k] = indices;
  if (new Set(indices).size !== 3) throw Error('Cardan needs three distinct axes');
  const sign = ([0,1,2].includes(i) && (
    (i === 0 && j === 1) || (i === 1 && j === 2) || (i === 2 && j === 0))) ? 1 : -1;
  const size = Math.hypot(q[0],q[1],q[2],q[3]);
  if (!size || !Number.isFinite(size)) throw Error('invalid quaternion');
  const [x,y,z,w] = q.map(c => c / size);
  const matrix = [
    [1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
    [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
    [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)],
  ];
  const b = Math.asin(Math.max(-1,Math.min(1,sign*matrix[i][k])));
  let a:number, c:number;
  if (Math.abs(Math.cos(b)) < 1e-7) {
    // At exact gimbal lock, the physically equivalent third component is set to zero.
    a = Math.atan2(sign*matrix[k][j], matrix[j][j]); c = 0;
  } else {
    a = Math.atan2(-sign*matrix[j][k], matrix[k][k]);
    c = Math.atan2(-sign*matrix[i][j], matrix[i][i]);
  }
  return [a,b,c].map((r,n) => r*180/Math.PI*(axes[n].startsWith('-') ? -1:1)) as [number,number,number];
}
