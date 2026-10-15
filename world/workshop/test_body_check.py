"""Only synthetic skeletons; no proprietary/person-specific avatar data."""
import copy
import unittest
from body_model import build_model
from body_check import LIMITS, evaluate, quiet_frames, static_support, resting_arm_limit

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
    return {"fps":30,"rest":rest,
            "bones":{n:{"position":[v[:]]*16,"rotation":[[0,0,0,1]]*16} for n,v in p.items()},
            "skin":{}, "bodySkin":{n:{"rest":[[v[0],v[1]+.8 - (
                                               .145 if n == "hips" or
                                               posture == "lying" and n in ("spine","chest","head","leftLowerLeg","rightLowerLeg")
                                               else .065 if n.endswith("Foot") else .025),v[2]+.025],
                                             [v[0],v[1]+.8 - (
                                               .145 if n == "hips" or
                                               posture == "lying" and n in ("spine","chest","head","leftLowerLeg","rightLowerLeg")
                                               else .065 if n.endswith("Foot") else .025),v[2]-.025]]}
                                   for n,v in p.items()}}


class BodyChecksTest(unittest.TestCase):
    def test_model_y_up_and_bones(self):
        s=sample()
        p=build_model(s)
        self.assertEqual(tuple(p.model.opt.gravity),(0.,-9.81,0.))
        self.assertLess(p.alignment_error(s,0)[0],.001)
        self.assertEqual(p.fallback,[])

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
        s["bodySkin"]["hips"]["rest"]=[[0, .8 + .10, .025],[0,.8+.10,-.025]]
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
        s["bones"]["leftLowerLeg"]["position"]=[[-.4,.47,-.12]]*16
        s["bones"]["leftFoot"]["position"]=[[-.69,.47,-.12]]*16
        result=static_support(build_model(s),s,0)
        self.assertGreater(result["effort_m"],before["effort_m"])
        self.assertGreater(result["effort_m"], resting_arm_limit(build_model(s),s))
        self.assertFalse(evaluate(s)["support"]["pass"])

if __name__ == "__main__":
    unittest.main()
