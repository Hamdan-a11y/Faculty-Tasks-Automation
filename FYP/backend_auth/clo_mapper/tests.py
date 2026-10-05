from __future__ import annotations

import io
from pathlib import Path

from django.test import TestCase

from . import services
from .models import CLO, Question


class CloMapperServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.fixture_dir = Path(__file__).resolve().parent / "tests" / "fixtures"
        cls.clo_file = cls.fixture_dir / "clo_example.docx"
        cls.questions_file = cls.fixture_dir / "questions_example.docx"

    def test_docx_clo_extraction(self):
        with self.clo_file.open("rb") as handle:
            clos = services.extract_clos_from_docx(io.BytesIO(handle.read()))
        self.assertGreaterEqual(len(clos), 3)
        self.assertEqual(clos[0]["code"].upper(), "CLO1")
        self.assertTrue(any("CLO2" == row["code"].upper() for row in clos))

    def test_question_extraction_docx_and_text(self):
        with self.questions_file.open("rb") as handle:
            questions = services.extract_questions_from_docx(io.BytesIO(handle.read()))
        self.assertGreaterEqual(len(questions), 3)

        text_questions = services.extract_questions_from_text(
            "Q1: Describe abstraction. Q2: What is polymorphism?"
        )
        self.assertEqual(len(text_questions), 2)

    def test_mapping_scores_range(self):
        clos = [
            CLO.objects.create(code="CLO1", description="Understand data structures and algorithms"),
            CLO.objects.create(code="CLO2", description="Apply object oriented programming principles"),
        ]
        questions = [
            Question.objects.create(text="Explain what a stack is.", assessment_type="quiz"),
            Question.objects.create(text="How do you achieve encapsulation?", assessment_type="quiz"),
        ]

        results = services.map_questions_to_clos(questions, clos)
        self.assertTrue(results)
        for result in results:
            self.assertGreaterEqual(result.score, 0.0)
            self.assertLessEqual(result.score, 1.0)
