"""Versioned contract tests; no model/network/DB calls."""
import unittest
from app import training_spec_v3 as s

class TestSixtyChoiceBlank(unittest.TestCase):
    def paper(self):
        return [
            dict(subject=subject,question_type=kind,max_points=2,rubric=[{"points":2}])
            for subject in s.SUBJECTS for kind,number in s.COUNTS.items()
            for _ in range(number)
        ]
    def test_contract(self):
        self.assertEqual(s.TOTAL_QUESTIONS, 60)
        self.assertEqual(s.TOTAL_POINTS, 120)
        self.assertEqual(s.COUNTS, {"single_choice":15,"fill_blank":15})
        self.assertEqual(s.QUESTIONS_PER_SUBJECT, 30)
        s.validate_blueprint(s.default_blueprint())
        full = s.formal_specification()
        self.assertEqual(full["points"], s.POINTS)
        self.assertEqual(full["points_total"], 120)
        self.assertEqual(full["points_by_type"], s.POINTS)
        self.assertEqual(len(s.validate_items(self.paper())),60)
    def test_refuses_28_q_and_essay(self):
        with self.assertRaises(ValueError):s.validate_items(self.paper()[:28])
        p=self.paper();p[0]["question_type"]="essay"
        with self.assertRaises(ValueError):s.validate_items(p)
    def test_refuses_type_scoring_and_bad_rubric(self):
        p=self.paper();p[0]["max_points"]=3
        with self.assertRaises(ValueError):s.validate_items(p)
        p=self.paper();p[0]["rubric"]=[{"points":1}]
        with self.assertRaises(ValueError):s.validate_items(p)
    def test_refuses_mutated_blueprint(self):
        bp=s.default_blueprint();bp["quotas"]["biochemistry"]["fill_blank"]=14
        with self.assertRaises(ValueError):s.validate_blueprint(bp)
    def test_pydantic_live_blueprint_accepts_only_v3_contract(self):
        from app.question_quality import Blueprint
        self.assertEqual(Blueprint.model_validate(s.default_blueprint()).specification, s.SPECIFICATION)
        bad = s.default_blueprint()
        bad["quotas"]["cell_biology"]["term"] = 1
        with self.assertRaises(ValueError):
            Blueprint.model_validate(bad)

    def test_bool_is_not_integer_quota(self):
        bp=s.default_blueprint();bp["quotas"]["biochemistry"]["fill_blank"]=True
        with self.assertRaises(ValueError):s.validate_blueprint(bp)
if __name__=="__main__":unittest.main()
