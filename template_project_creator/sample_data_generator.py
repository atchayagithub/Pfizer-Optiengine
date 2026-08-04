"""Deterministic, configuration-driven sample-data generators."""
from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .config import ConfigurationError


class SampleDataGenerator:
    """Writes declared fixtures before DSS objects are created."""

    def __init__(self, specs: list[dict[str, Any]], output_dir: Path):
        self.specs, self.output_dir = specs, output_dir

    def generate(self) -> dict[str, Path]:
        generated: dict[str, Path] = {}
        self.output_dir.mkdir(parents=True, exist_ok=True)
        for spec in self.specs:
            generator = spec.get("generator", "flight").lower()
            path = self.output_dir / spec.get("filename", f"{spec['name']}.csv")
            if generator == "flight":
                self._flight_csv(path, int(spec.get("records", 100)), int(spec.get("seed", 42)))
            elif generator == "employees":
                self._employees_csv(path, int(spec.get("records", 100)), int(spec.get("seed", 42)))
            elif generator == "departments":
                self._departments_csv(path, int(spec.get("records", 10)), int(spec.get("seed", 42)))
            elif generator in {"employee_merged", "employees_merged"}:
                self._employee_merged_csv(path, int(spec.get("records", 100)), int(spec.get("seed", 42)))
            elif generator in {"hospital_patients", "patients"}:
                self._hospital_patients_csv(path, int(spec.get("records", 200)), int(spec.get("seed", 42)))
            elif generator in {"hospital_admissions", "admissions"}:
                self._hospital_admissions_csv(path, int(spec.get("records", 250)), int(spec.get("seed", 42)))
            elif generator in {"hospital_diagnoses", "diagnoses"}:
                self._hospital_diagnoses_csv(path, int(spec.get("records", 250)), int(spec.get("seed", 42)))
            elif generator in {"hospital_medications", "medications"}:
                self._hospital_medications_csv(path, int(spec.get("records", 300)), int(spec.get("seed", 42)))
            elif generator in {"hospital_features", "patient_features"}:
                self._hospital_features_csv(path, int(spec.get("records", 250)), int(spec.get("seed", 42)))
            else:
                raise ConfigurationError(f"Unsupported sample-data generator {generator!r}")
            generated[spec["name"]] = path
        return generated

    @staticmethod
    def _flight_csv(path: Path, records: int, seed: int) -> None:
        if records < 1:
            raise ConfigurationError("sample_data.records must be at least 1")
        randomizer = random.Random(seed)
        airports = ["JFK", "LAX", "ORD", "ATL", "DFW", "SFO", "SEA", "MIA"]
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=[
                "flight_id", "origin", "destination", "distance", "air_time", "delay", "cancelled",
            ])
            writer.writeheader()
            for number in range(1, records + 1):
                origin = randomizer.choice(airports)
                destination = randomizer.choice([x for x in airports if x != origin])
                distance = randomizer.randint(180, 2800)
                cancelled = int(randomizer.random() < 0.035)
                delay = randomizer.randint(0, 160) if not cancelled else randomizer.randint(0, 35)
                air_time = 0 if cancelled else max(35, round(distance / randomizer.uniform(6.6, 8.2)))
                writer.writerow({"flight_id": f"FL{number:04d}", "origin": origin,
                                 "destination": destination, "distance": distance,
                                 "air_time": air_time, "delay": delay, "cancelled": cancelled})

    @staticmethod
    def _department_rows(records: int, seed: int) -> list[dict[str, Any]]:
        if records < 1:
            raise ConfigurationError("sample_data.records must be at least 1")
        randomizer = random.Random(seed)
        names = ["Engineering", "Sales", "Human Resources", "Finance", "Marketing",
                 "Operations", "Customer Success", "Legal", "Product", "Analytics"]
        locations = ["New York", "San Francisco", "Chicago", "Austin", "Seattle"]
        managers = ["Ava Patel", "Noah Williams", "Mia Chen", "Liam Brown", "Sophia Garcia",
                    "Ethan Davis", "Olivia Martin", "James Wilson", "Emma Taylor", "Lucas Moore"]
        return [{"department_id": f"D{number:03d}", "department_name": names[(number - 1) % len(names)],
                 "manager": managers[(number - 1) % len(managers)],
                 "location": randomizer.choice(locations),
                 "budget": randomizer.randrange(250_000, 2_000_001, 25_000)}
                for number in range(1, records + 1)]

    @classmethod
    def _employee_rows(cls, records: int, seed: int) -> list[dict[str, Any]]:
        if records < 1:
            raise ConfigurationError("sample_data.records must be at least 1")
        randomizer = random.Random(seed)
        first_names = ["Aarav", "Ananya", "Ishaan", "Diya", "Arjun", "Kavya", "Rohan", "Meera"]
        last_names = ["Sharma", "Iyer", "Kapoor", "Nair", "Reddy", "Singh", "Das", "Joshi"]
        genders = ["Female", "Male", "Non-binary"]
        education = ["Bachelor", "Master", "MBA", "PhD"]
        cities = ["Bengaluru", "Mumbai", "Delhi", "Hyderabad", "Pune", "Chennai"]
        department_count = 10
        rows = []
        for number in range(1, records + 1):
            experience = randomizer.randint(0, 25)
            performance = round(randomizer.uniform(2.4, 5.0), 2)
            age = max(21, min(65, 22 + experience + randomizer.randint(-2, 12)))
            salary = round(35_000 + experience * 4_200 + performance * 7_500 + randomizer.randint(-6_000, 6_000), 2)
            rows.append({"employee_id": f"E{number:04d}",
                         "employee_name": f"{randomizer.choice(first_names)} {randomizer.choice(last_names)}",
                         "age": age, "gender": randomizer.choice(genders),
                         "department_id": f"D{randomizer.randint(1, department_count):03d}",
                         "salary": salary, "experience": experience,
                         "performance_score": performance,
                         "education": randomizer.choice(education), "city": randomizer.choice(cities)})
        return rows

    @classmethod
    def _employees_csv(cls, path: Path, records: int, seed: int) -> None:
        rows = cls._employee_rows(records, seed)
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader(); writer.writerows(rows)

    @classmethod
    def _departments_csv(cls, path: Path, records: int, seed: int) -> None:
        rows = cls._department_rows(records, seed)
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader(); writer.writerows(rows)

    @classmethod
    def _employee_merged_csv(cls, path: Path, records: int, seed: int) -> None:
        departments = {row["department_id"]: row for row in cls._department_rows(10, seed)}
        rows = []
        for employee in cls._employee_rows(records, seed):
            department = departments[employee["department_id"]]
            rows.append({**employee, **{key: value for key, value in department.items() if key != "department_id"}})
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader(); writer.writerows(rows)

    @staticmethod
    def _hospital_patients(records: int, seed: int) -> list[dict[str, Any]]:
        if records < 1:
            raise ConfigurationError("sample_data.records must be at least 1")
        rng = random.Random(seed)
        first = ["Aarav", "Ananya", "Arjun", "Diya", "Ishaan", "Kavya", "Meera", "Rohan", "Saanvi", "Vihaan"]
        last = ["Sharma", "Iyer", "Kapoor", "Nair", "Patel", "Reddy", "Singh", "Das"]
        cities = ["Bengaluru", "Chennai", "Delhi", "Hyderabad", "Mumbai", "Pune"]
        return [{"patient_id": f"P{number:04d}",
                 "patient_name": f"{rng.choice(first)} {rng.choice(last)}",
                 "age": rng.randint(1, 92), "gender": rng.choice(["Female", "Male", "female", "M"]),
                 "blood_group": rng.choice(["A+", "A-", "B+", "B-", "AB+", "O+", "O-"]),
                 "city": rng.choice(cities), "phone": f"9{rng.randint(100000000, 999999999)}"}
                for number in range(1, records + 1)]

    @classmethod
    def _hospital_patients_csv(cls, path: Path, records: int, seed: int) -> None:
        rows = cls._hospital_patients(records, seed)
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)

    @staticmethod
    def _hospital_patient_id(rng: random.Random) -> str:
        # Raw hospital fixtures intentionally share this fixed cohort of 200 patients.
        return f"P{rng.randint(1, 200):04d}"

    @classmethod
    def _hospital_admission_rows(cls, records: int, seed: int) -> list[dict[str, Any]]:
        if records < 1:
            raise ConfigurationError("sample_data.records must be at least 1")
        rng, start = random.Random(seed + 1), date(2024, 1, 1)
        departments = ["Cardiology", "Emergency", "General Medicine", "Neurology", "Oncology", "Orthopedics", "Pediatrics"]
        doctors = ["Dr. Banerjee", "Dr. Iqbal", "Dr. Menon", "Dr. Rao", "Dr. Shah", "Dr. Thomas"]
        rows = []
        for number in range(1, records + 1):
            admitted = start + timedelta(days=rng.randint(0, 700))
            discharged = admitted + timedelta(days=rng.randint(1, 15))
            rows.append({"admission_id": f"A{number:05d}", "patient_id": cls._hospital_patient_id(rng),
                         "admission_date": admitted.isoformat(), "discharge_date": discharged.isoformat(),
                         "department": rng.choice(departments), "doctor": rng.choice(doctors),
                         "admission_type": rng.choice(["Emergency", "Elective", "Referral"])})
        return rows

    @classmethod
    def _hospital_admissions_csv(cls, path: Path, records: int, seed: int) -> None:
        rows = cls._hospital_admission_rows(records, seed)
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)

    @classmethod
    def _hospital_diagnoses_csv(cls, path: Path, records: int, seed: int) -> None:
        rng = random.Random(seed + 2)
        diagnoses = [("I10", "Essential hypertension"), ("E11", "Type 2 diabetes mellitus"),
                     ("J18", "Pneumonia"), ("M17", "Osteoarthritis"), ("N39", "Urinary tract infection")]
        rows = [{"diagnosis_id": f"D{number:05d}", "patient_id": cls._hospital_patient_id(rng),
                 "diagnosis_code": code, "diagnosis_description": description,
                 "severity": rng.choice(["Low", "Moderate", "High"])}
                for number, (code, description) in
                ((number, rng.choice(diagnoses)) for number in range(1, records + 1))]
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)

    @classmethod
    def _hospital_medications_csv(cls, path: Path, records: int, seed: int) -> None:
        rng = random.Random(seed + 3)
        medicines = ["Amoxicillin", "Atorvastatin", "Metformin", "Paracetamol", "Pantoprazole"]
        rows = [{"medication_id": f"M{number:05d}", "patient_id": cls._hospital_patient_id(rng),
                 "medicine_name": rng.choice(medicines), "dosage": rng.choice(["250 mg", "500 mg", "10 mg"]),
                 "frequency": rng.choice(["Once daily", "Twice daily", "Every 8 hours"])}
                for number in range(1, records + 1)]
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)

    @classmethod
    def _hospital_features_csv(cls, path: Path, records: int, seed: int) -> None:
        """Synthetic training fixture matching the numerical feature contract of the Flow."""
        patients = {row["patient_id"]: row for row in cls._hospital_patients(200, seed)}
        admissions = cls._hospital_admission_rows(records, seed)
        rng = random.Random(seed + 4)
        rows = []
        for admission in admissions:
            patient = patients[admission["patient_id"]]
            length_of_stay = (date.fromisoformat(admission["discharge_date"]) - date.fromisoformat(admission["admission_date"])).days
            severity_score = rng.choice([1, 2, 3])
            risk = int(patient["age"] >= 70 or length_of_stay >= 9 or severity_score == 3)
            rows.append({"age": patient["age"], "length_of_stay": length_of_stay,
                         "severity_score": severity_score, "risk_label": risk})
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
