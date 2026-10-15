"""Only synthetic skeletons; no proprietary/person-specific avatar data."""
import copy
import json
import unittest
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation
from body_model import build_model
from body_check import LIMITS, evaluate, quiet_frames, static_support, resting_arm_limit, surface_contacts

def sample(posture="lying"):
    # Coordinates below are a synthetic 1.2m stick figure, not a real avatar.
    if posture == "lying":
        p = {"hips":[0,.145,0], "spine":[.18,.145,0], "chest":[.36,.145,0],
             "neck":[.48,.145,0], "head":[.62,.145,0]}
        for side,z in (("left",-.12),("right",.12)):
            p.update({side+"Shoulder":[.36,.145,z],side+"UpperArm":[.33,.145,z*2],
                      side+"LowerArm":[.3,.145,z*4],side+"Hand":[.27,.145,z*5.5],
                      side+"UpperLeg":[-.12,.145,z],side+"LowerLeg":[-.42,.145,z],
                      side+"Foot":[-.72,.145,z]})
    else:
        p = {"hips":[0,.145,0], "spine":[0,.35,0],"chest":[0,.57,0],
             "neck":[0,.72,0],"head":[0,.86,0]}
        for side,x in (("left",-.13),("right",.13)):
            p.update({side+"Shoulder":[x,.57,0],side+"UpperArm":[x*1.8,.52,0],
                      side+"LowerArm":[x*2.3,.4,0],side+"Hand":[x*2.3,.3,0],
                      side+"UpperLeg":[x,.17,.13],side+"LowerLeg":[x,.14,.43],
                      side+"Foot":[x,.06,.53]})
    rest = {n:[v[0],v[1]+.8,v[2]] for n,v in p.items()}
    surface = {n:{"rest":[[v[0],v[1] - (
                    .145 if n == "hips" or
                    posture == "lying" and n in ("spine","chest","head","leftLowerLeg","rightLowerLeg")
                    else .065 if n.endswith("Foot") else .025),v[2]+.025],
                            [v[0],v[1] - (
                    .145 if n == "hips" or
                    posture == "lying" and n in ("spine","chest","head","leftLowerLeg","rightLowerLeg")
                    else .065 if n.endswith("Foot") else .025),v[2]-.025]]}
                    for n,v in p.items()}
    return {"fps":30,"rest":rest,
            "bones":{n:{"position":[v[:]]*16,"rotation":[[0,0,0,1]]*16} for n,v in p.items()},
            "skin":{}, "bodySkin":surface,
            "tpose":{"fps":30, "rest":p, "bodySkin":surface,
                     "bones":{n:{"position":[v[:]],"rotation":[[0,0,0,1]]} for n,v in p.items()},
                     "skin":{}}}


class BodyChecksTest(unittest.TestCase):
    def test_model_y_up_and_bones(self):
        s=sample()
        p=build_model(s)
        self.assertEqual(tuple(p.model.opt.gravity),(0.,-9.81,0.))
        self.assertLess(p.alignment_error(s,0)[0],.001)
        self.assertEqual(p.fallback,[])

    def test_raw_forearm_twist_moves_surface_off_floor(self):
        # 90° axial roll: bone endpoints remain fixed but underside lifts.
        s = sample("lying")
        for name in ("leftLowerArm", "leftHand"):
            s["rest"][name] = [.4 if name == "leftLowerArm" else .6, .85, .12]
            s["bones"][name]["position"] = [[s["rest"][name][0], .05, .12]] * 16
        s["tpose"]["bodySkin"]["leftLowerArm"]["rest"] = [
            [.45, .015, .12], [.55, .015, .12]]
        p = build_model(s)
        before = [name for name, _ in surface_contacts(p, s, 0)]
        self.assertIn("leftLowerArm", before)
        roll = Rotation.from_euler("x", 90, degrees=True).as_quat().tolist()
        s["bones"]["leftLowerArm"]["rotation"] = [roll] * 16
        after = [name for name, _ in surface_contacts(p, s, 0)]
        self.assertNotIn("leftLowerArm", after)
        self.assertEqual(after.count("leftLowerArm"), 0)

    def test_raw_world_rotation_is_independent_of_dictionary_order(self):
        s = sample()
        p = build_model(s)
        baseline = p.alignment_error(s, 0)[0]
        s["tpose"]["rest"] = dict(reversed(list(s["tpose"]["rest"].items())))
        p = build_model(s)
        self.assertAlmostEqual(baseline, p.alignment_error(s, 0)[0], places=6)

    def test_separate_tpose_is_used_for_arm_limit(self):
        s = sample("seated")
        baseline = resting_arm_limit(build_model(s), s)
        tpose = copy.deepcopy(s["tpose"])
        for side, sign in (("left", -1), ("right", 1)):
            for name, length in (("UpperArm", .23), ("LowerArm", .45), ("Hand", .66)):
                pos = tpose["rest"][side + name]
                pos[0] = sign * length
                tpose["bodySkin"][side + name]["rest"] = [
                    [pos[0], pos[1] + .025, pos[2]], [pos[0], pos[1] - .025, pos[2]]
                ]
        # Independent baseline is deliberately a different pose.
        s["tpose"] = tpose
        actual = resting_arm_limit(build_model(s), s)
        self.assertNotAlmostEqual(actual, baseline, places=5)

    def test_normalized_elbows_obey_semantic_bend_signs(self):
        s = sample()
        s["tpose"] = copy.deepcopy(s)
        left = Rotation.from_euler("Y", -84, degrees=True).as_quat().tolist()
        right = Rotation.from_euler("Y", 76, degrees=True).as_quat().tolist()
        s["bones"]["leftLowerArm"]["rotation"] = [left] * 16
        s["bones"]["rightLowerArm"]["rotation"] = [right] * 16
        p = build_model(s)
        p.pose(s, 0)
        for name in ("leftLowerArm", "rightLowerArm"):
            j = p.model.joint(f"{name}_bend").id
            angle = p.data.qpos[p.model.jnt_qposadr[j]]
            lower, upper = p.model.jnt_range[j]
            self.assertLessEqual(lower, angle)
            self.assertLessEqual(angle, upper)
        left_j = p.model.joint("leftLowerArm_bend").id
        right_j = p.model.joint("rightLowerArm_bend").id
        self.assertAlmostEqual(float(np.rad2deg(p.data.qpos[p.model.jnt_qposadr[left_j]])), 84, places=4)
        self.assertAlmostEqual(float(np.rad2deg(p.data.qpos[p.model.jnt_qposadr[right_j]])), 76, places=4)

    def test_shared_scipy_typescript_cardan_golden(self):
        from body_model import RANGES
        fixtures = json.loads((Path(__file__).parents[1] / "sea" /
                               "joint-angles.fixtures.json").read_text(encoding="utf8"))
        for row in fixtures:
            defs = RANGES["joints"][row["bone"]]
            axes = [defs[k]["axis"] for k in ("bend", "offAxis", "twist")]
            seq = "".join(a[-1].upper() for a in axes)
            angles = Rotation.from_quat(row["quaternion"]).as_euler(seq, degrees=True)
            expected = [float(deg) * (-1 if axis.startswith("-") else 1)
                        for deg, axis in zip(angles, axes)]
            self.assertTrue(np.allclose(expected, row["angles"], atol=2e-5))

    def test_signed_joint_axis_mirrors_and_major_tpose_twist(self):
        from body_model import RANGES
        for side in ("left", "right"):
            for suffix, major in (("LowerArm","x"),("UpperArm","x"),
                                  ("Shoulder","x"),("Hand","x"),
                                  ("UpperLeg","y"),("LowerLeg","y"),
                                  ("Foot","z"),("Toes","z")):
                name = side + suffix
                self.assertEqual(RANGES["joints"][name]["twist"]["axis"].lstrip("-"), major)
        for suffix in ("LowerArm","UpperArm","Shoulder","Hand",
                       "LowerLeg","UpperLeg","Foot","Toes"):
            left, right = (RANGES["joints"][side + suffix] for side in ("left","right"))
            for kind in ("bend","offAxis","twist"):
                l = left[kind]["axis"]
                r = right[kind]["axis"]
                expected = ("" if l.startswith("-") else "-") + l[-1] if l[-1] in "yz" else l
                self.assertEqual(r,expected)

    def test_real_sample_without_true_tpose_is_rejected(self):
        s = sample()
        del s["tpose"]
        with self.assertRaisesRegex(ValueError, "T-pose"):
            evaluate(s)

    def test_nonadjacent_crossing(self):
        s=sample("seated")
        for n,a,b in (("leftLowerArm",[-.23,.4,0],[.2,.4,0]),
                      ("rightLowerArm",[.23,.4,0],[-.2,.4,0])):
            s["bones"][n]["position"]=[a]*16
            side = "left" if n.startswith("left") else "right"
            s["bones"][side+"Hand"]["position"]=[b]*16
        self.assertGreater(build_model(s).collisions(s,0)["depth_m"],0)

    def test_neighbor_is_not_a_collision(self):
        p=build_model(sample())
        self.assertTrue(p._near("leftUpperArm","leftLowerArm"))
        self.assertTrue(p._near("leftUpperArm","leftHand"))
        self.assertFalse(p._near("leftLowerArm","rightLowerArm"))

    def test_high_hips_exempt(self):
        s=sample()
        s["rest"]["hips"][1]=.2
        self.assertEqual(quiet_frames(s),[])

    def test_unseated_airborne_body_fails(self):
        s=sample("seated")
        # Remove pelvis support by lifting only its synthetic surface sample.
        s["tpose"]["bodySkin"]["hips"]["rest"]=[[0, .245, .025],[0,.245,-.025]]
        r=static_support(build_model(s),s,0)
        self.assertGreater(r["root_residual"],.01)
        self.assertFalse(evaluate(s)["support"]["pass"])

    def test_quiet_low_hips_observed(self):
        self.assertTrue(quiet_frames(sample()))
        self.assertTrue(quiet_frames(sample("seated")))

    def test_synthetic_flat_and_seated_balance(self):
        for shape in ("lying", "seated"):
            with self.subTest(shape=shape):
                s=sample(shape)
                r=static_support(build_model(s),s,0)
                self.assertLess(r["root_residual"],.01)
                self.assertLessEqual(r["effort_m"], round(resting_arm_limit(build_model(s),s), 6))
                self.assertTrue(evaluate(s)["support"]["pass"])

    def test_side_lying_contact(self):
        s=sample("lying")
        # Fold arms onto the torso, keep the lower side, torso and hip grounded.
        for bone, point in {
            "leftLowerArm":[.28,.22,-.22], "leftHand":[.20,.23,-.15],
            "rightLowerArm":[.30,.20,.25], "rightHand":[.21,.17,.17],
        }.items():
            s["bones"][bone]["position"]=[point]*16
        result=static_support(build_model(s),s,0)
        self.assertLess(result["root_residual"],.01)

    def test_raised_leg_requires_higher_effort(self):
        s=sample()
        before=static_support(build_model(s),s,0)
        angle = Rotation.from_euler("z", -85, degrees=True).as_quat().tolist()
        s["bones"]["leftUpperLeg"]["rotation"] = [angle] * 16
        p = build_model(s)
        p.pose(s, 0)
        for name in p.names:
            position = p.data.xpos[p.model.body(name).id].tolist()
            s["bones"][name]["position"] = [position] * 16
        result=static_support(build_model(s),s,0)
        self.assertGreater(result["effort_m"],before["effort_m"])
        self.assertGreater(result["effort_m"], resting_arm_limit(build_model(s),s))
        self.assertFalse(evaluate(s)["support"]["pass"])

if __name__ == "__main__":
    unittest.main()
