from __future__ import annotations

import csv
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jolup_rules_all.app.rules import loader

from app.schemas.evaluation import Source
from app.schemas.rules import (
    CourseOption, CourseRequirement, CreditCap, CreditRequirement, CreditTreatment,
    GraduationRule, NonacademicRequirement, VerificationRequirement,
)
from app.schemas.student import StudentProfile

STATUS_LABELS = {"portfolio": "졸업 포트폴리오", "interview": "졸업 면담", "topcit": "TOPCIT",
                 "research_report": "연구보고서", "presentation": "연구 발표",
                 "major_competency_assessment": "전공 학업역량평가"}
MULTI_MAJOR_KEYS = {"double_major": "double_or_linked_or_convergence",
                   "interdisciplinary": "double_or_linked_or_convergence", "minor": "minor_1_only"}


class BundleProfileError(ValueError):
    pass


class BundleUnavailableError(LookupError):
    pass


def get_bundle_loader():
    from jolup_rules_all.app.rules import loader

    return loader


def matching_entries(student: StudentProfile) -> list[loader.RuleSetEntry]:
    loader = get_bundle_loader()
    return [entry for entry in loader.load_index()
            if entry.major == student.major and entry.admission_from <= student.admission_year <= entry.admission_to]


def student_status(student: StudentProfile) -> loader.Status:
    if student.student_status is not None:
        if student.transfer_student and student.student_status not in {"general_transfer", "bachelor_transfer"}:
            raise BundleProfileError("편입 여부와 입학 구분을 확인해 주세요.")
        if student.international_student and student.student_status not in {
            "foreign", "general_transfer", "bachelor_transfer",
        }:
            raise BundleProfileError("외국인 여부와 입학 구분을 확인해 주세요.")
        if student.disabled_student and student.student_status != "disabled":
            raise BundleProfileError("장애학생 여부와 학생 구분을 확인해 주세요.")
        return student.student_status
    if student.transfer_student:
        raise BundleProfileError("일반편입 또는 학사편입을 student_status에 선택해 주세요.")
    if student.disabled_student and student.international_student:
        raise BundleProfileError("동시에 적용되는 외국인·장애학생 기준은 확인이 필요합니다.")
    if student.disabled_student:
        return "disabled"
    if student.international_student:
        return "foreign"
    return "new"


def _catalog(path: str) -> dict[str, dict]:
    loader = get_bundle_loader()
    with (loader.RULES_DIR / path).open(encoding="utf-8-sig", newline="") as stream:
        return {row["code"]: row for row in csv.DictReader(stream)}


def find_catalog(entries: list[loader.RuleSetEntry]) -> tuple[dict, list[dict]]:
    loader = get_bundle_loader()
    curricula = [loader.load_rule_set(entry.id)["curriculum"] for entry in entries]
    if len({item["catalog_file"] for item in curricula}) != 1:
        raise ValueError("The selected cohort has multiple different catalogs")
    return curricula[0], list(_catalog(curricula[0]["catalog_file"]).values())


def _retake_pairs() -> dict[str, list[str]]:
    loader = get_bundle_loader()
    with (loader.RULES_DIR / "common/equivalences.csv").open(encoding="utf-8-sig", newline="") as stream:
        return {row["old_code"]: [row["new_code"]] for row in csv.DictReader(stream) if row["type"] == "동일과목"}


class RuleBundle:
    def resolve(self, student: StudentProfile) -> GraduationRule:
        loader = get_bundle_loader()
        if student.major == "plsop" and student.abeek is None:
            raise BundleProfileError("플랫폼SW&데이터과학전공은 ABEEK 이수 여부를 선택해야 합니다.")
        if student.major != "plsop" and student.abeek:
            raise BundleProfileError("이 전공에는 ABEEK 별도 규칙이 등록되어 있지 않습니다.")
        status = student_status(student)
        multi = MULTI_MAJOR_KEYS.get(student.multi_major, student.multi_major)
        try:
            raw = loader.select_rule_set(student.major, student.admission_year, student.abeek)
        except loader.NoRuleSet as exc:
            raise BundleUnavailableError(str(exc)) from exc
        tracks = raw.get("tracks", {})
        if (tracks and student.track not in tracks) or (not tracks and student.track is not None):
            raise BundleProfileError("해당 전공과 학번에 등록된 트랙을 선택해 주세요.")
        track = tracks.get(student.track, {})
        variants = track.get("variants", {})
        if student.track_variant is not None and student.track_variant not in variants:
            raise BundleProfileError("해당 트랙에 등록된 세부 유형을 선택해 주세요.")
        variant = variants.get(student.track_variant, {})
        program_end = raw.get("program_end", {}).get("last_graduation")
        if program_end and student.expected_graduation_date and student.expected_graduation_date > program_end:
            raise BundleProfileError(f"ABEEK 이수는 {program_end} 졸업까지만 적용됩니다.")

        resolved = loader.resolve(raw, status, multi, student.track, student.admission_year)
        return _build_rule(student, status, multi, raw, resolved, track, variant, program_end)


def _build_rule(student, status, multi, raw, resolved, track, variant, program_end):
    loader = get_bundle_loader()
    source_items = loader.load_common("sources")["sources"]
    sources = {item["id"]: Source(title=item["title"], url=item.get("url"), reference=item.get("date"))
               for item in source_items}

    def cite(section):
        return [sources[key] for key in section.get("source", raw["sources"])]

    credits = raw["credits"]
    liberal = credits["liberal_arts"]
    required = raw["required_courses"]
    policies = loader.load_common("policies")
    qualifications = loader.load_common("graduation_qualification")["items"]
    statuses = loader.load_common("statuses")["statuses"]
    field = loader.load_common("field_practice")
    curriculum = raw["curriculum"]
    catalog = _catalog(curriculum["catalog_file"])
    course_groups = []
    equivalent = {}
    for item in required["all_of"]:
        if item.get("equivalent_codes"):
            equivalent[item["code"]] = item["equivalent_codes"]
    for is_liberal in (False, True):
        options = [CourseOption(course_code=item["code"], course_name=item["name"])
                   for item in required["all_of"] if item["code"].startswith("CLTR") == is_liberal]
        if options:
            course_groups.append(CourseRequirement(
                id="required-liberal" if is_liberal else "required-major",
                name="필수 교양·전공기반 과목" if is_liberal else "전공필수 교과목",
                courses=options, choose=len(options), require_department=not is_liberal and bool(required.get("offered_by")),
                required_transcript_category=required.get("must_be_classified_as") if not is_liberal else None,
                reject_transfer_courses=status in {"general_transfer", "bachelor_transfer"},
                sources=cite(required),
            ))

    records = []
    verification = []
    notes = [curriculum["basis"]]
    if liberal.get("rule"):
        notes.append(liberal["rule"])

    def record(key, name, minimum, section, *, unit=None, details=()):
        records.append(NonacademicRequirement(
            id=key, name=name, minimum=minimum, unit=unit, details=list(details), sources=cite(section),
        ))

    def confirm(key, name, section):
        verification.append(VerificationRequirement(id=key, name=name, sources=cite(section)))

    if program_end:
        notes.append(f"ABEEK 이수 적용은 {program_end} 졸업까지")
        if not student.expected_graduation_date:
            confirm("abeek-graduation-month", "ABEEK 적용 여부를 판단하려면 졸업 예정 연·월 확인", raw["program_end"])
    if status != "new":
        notes.append(statuses[status]["label"] + " 기준 적용")
    if resolved.qualification_exempt:
        names = {"practical_english": "실용영어", "software": "소프트웨어", "counseling": "상담"}
        notes.append("학교 졸업자격인정제 면제 항목: " + ", ".join(names[key] for key in resolved.qualification_exempt))
    if resolved.liberal_arts_min is None:
        notes.append("학사편입 교양 인정" if status == "bachelor_transfer" else "학교 교양 최소학점 기준 미적용")
    if not resolved.early_graduation_allowed:
        notes.append("조기졸업 신청 대상 제외")

    if resolved.field_practice_credits:
        alternatives = [item["text"] + " (승인 필요)" for item in field["alternatives"]
                        if not item.get("only_for") or status in item["only_for"]]
        record("field-practice", "현장실습 또는 승인된 대체 실적", resolved.field_practice_credits,
               raw["field_practice"], unit="학점", details=alternatives)
    research = raw["field_practice"].get("research")
    if research:
        record("lab-semesters", "연구실 참여", research["lab_semesters"], raw["field_practice"], unit="학기")
        record("research-events", "연구행사 참여", research["research_events"], raw["field_practice"], unit="회")

    english = resolved.english
    if english.department_min_toeic is not None:
        english_min = variant.get("english", {}).get("min", english.department_min_toeic)
        details = [english.alternative] if english.alternative else []
        if raw["english"].get("inferred"):
            details.append("자료에 표시된 추정 기준")
        record("department-english", "학부 영어 요건 (TOEIC 기준)", english_min, raw["english"],
               unit="점", details=details)

    exempt = resolved.qualification_exempt
    practical = qualifications["practical_english"]
    if english.topik4_instead:
        record("practical-english", "실용영어 대체 (TOPIK)", 4, practical, unit="급")
    elif "practical_english" not in exempt:
        scores = [f"{item['test']} {item['min']}" for item in practical["accepted_scores"]]
        record("practical-english", "실용영어 졸업자격인정", 1, practical,
               details=[practical["validity"], "시험별 기준: " + ", ".join(scores)])
    if "software" not in exempt:
        software = qualifications["software"]
        record("software", "소프트웨어 졸업자격인정", 1, software,
               details=[software["rule"], "인정 대체 과목: " + ", ".join(software["department_substitute_codes"])])
    if "counseling" not in exempt:
        counseling = qualifications["counseling"]
        record("counseling", "지도교수 상담", counseling["min_sessions"], counseling, unit="회")

    gpa = policies["min_gpa"]
    record("gpa", "평점평균", gpa["value"], gpa, details=[f"{gpa['scale']} 만점, F 포함"])
    if resolved.sdg_min:
        record("sdg", "SDG 교양", resolved.sdg_min, liberal, unit="학점")
    if resolved.liberal_arts_areas_required:
        for area in liberal["areas"]:
            record(area["id"], "교양 영역: " + " / ".join(area["any_of"]), area["min_credits"], liberal,
                   unit="학점", details=["경북대 개설 과목"] if liberal.get("area_courses_must_be_offered_by_knu") else [])
    abeek = credits.get("abeek")
    if abeek:
        for key, name in (("basic_literacy", "기본소양"), ("msc", "전공기반"), ("engineering_major", "공학전공"),
                          ("design_min", "설계")):
            record("abeek-" + key, "ABEEK " + name, abeek[key], abeek, unit="학점",
                   details=[", ".join(f"{code} {amount}학점" for code, amount in abeek["design_courses"].items())]
                   if key == "design_min" else [])
        cap = liberal.get("excess_cap")
        if cap and cap["applies_to_admission_years"][0] <= student.admission_year <= cap["applies_to_admission_years"][1]:
            notes.append(f"기본소양·전공기반을 제외한 교양은 최대 {cap['max_counted_toward_total']}학점 인정")
            confirm("abeek-excess-cap", "ABEEK 교양 초과학점 제한 반영 확인", liberal)
    graduate = credits["major"].get("graduate_credits")
    if graduate:
        record("graduate-credits", "대학원 과목", graduate["min"], credits["major"], unit="학점")

    review = raw["graduation_review"]
    for key in review["items"]:
        alternative = review.get("interview_exempt_if") or review.get("interview_substitute")
        record("review-" + key, STATUS_LABELS[key], 1, review,
               details=["대체 또는 면제: " + alternative] if key == "interview" and alternative else [])

    if track.get("eligibility"):
        record("track-eligibility", track["eligibility"], 1, track)
    for item in track.get("requirements", []):
        key = item["id"]
        if key == "multi_major_optional":
            notes.append(item["text"])
        elif key == "multi_major_required":
            # The selected plan is not evidence that the other major was completed.
            record("track-" + key, item["text"], 1, track)
        elif item.get("one_of"):
            options = [CourseOption(course_code=code, course_name=catalog.get(code, {}).get("name", code))
                       for code in item["one_of"]]
            course_groups.append(CourseRequirement(
                id="track-" + key, name=item["text"], courses=options, choose=1,
                minimum_course_credits=item.get("min_credits", 0), require_department=True, sources=cite(track),
                reject_transfer_courses=status in {"general_transfer", "bachelor_transfer"},
                accept_equivalent_codes=False,
            ))
        else:
            record("track-" + key, item["text"], item.get("min_credits", 1), track)
    if variant:
        notes.append(variant["text"])
        record("track-knu-credits", "본교 이수", variant["knu_credits_min"], track, unit="학점")

    active = set(raw["needs_confirmation"])
    relevant = {
        "NC-AICOM-ENG-EXEMPT": english.exempt,
        "NC-TRANSFER-MINOR1": status in {"general_transfer", "bachelor_transfer"} and multi == "minor_1_only",
        "NC-GLSOP-800-EQUIV": english.department_min_toeic == 800 and not variant,
        "NC-PLSOP-ABEEK-BACH-BASIC": status == "bachelor_transfer",
        "NC-LIB-AREA-MAP": resolved.liberal_arts_areas_required,
    }
    for item in loader._read("needs_confirmation.json")["items"]:
        key = item["id"]
        if key in active and relevant.get(key, True):
            confirm(key, item["question"], {"source": item["sources"]})
    if resolved.field_practice_credits and student.major != "aicom":
        confirm("NC-FP-SCOPE", "현장실습 대체 기준의 전공별 적용 범위", field)
    for item in required["all_of"]:
        if item.get("co_requisite"):
            confirm("corequisite-" + item["code"], f"{item['name']} 관련 과목 {item['co_requisite']} 이수 기준", required)
    if required.get("prerequisites_enforced"):
        record("prerequisites", "필수 교과목 선수과목 인정 확인", 1, required)
    transfer = status in {"general_transfer", "bachelor_transfer"}
    if transfer:
        notes.append(statuses[status]["transfer_credit_cap"])

    caps = []
    future = policies["future_design"]
    if student.admission_year >= future["applies_from_admission_year"]:
        caps.append(CreditCap(maximum=future["max_counted"], code_prefix=future["prefix"]))
    if liberal.get("max_counted") is not None and resolved.liberal_arts_min is not None:
        caps.append(CreditCap(maximum=liberal["max_counted"], category="liberal_arts"))

    major_codes = {item["code"] for item in required["all_of"] if not item["code"].startswith("CLTR")}
    if not curriculum.get("inferred"):
        major_codes.update(code for code, row in catalog.items() if row["counts_toward"] == "major")
    treatments = {}
    if student.major == "glsop" and student.admission_year >= 2023:
        treatments["ITEC0402"] = CreditTreatment(category="general")

    credit_requirements = [
        CreditRequirement(id="total", name="총 졸업학점", area="total", minimum=resolved.total_credits,
                          sources=cite(credits["total"])),
        CreditRequirement(id="major", name="전공학점", area="major", minimum=resolved.major_credits,
                          sources=cite(credits["major"])),
    ]
    if resolved.liberal_arts_min is not None:
        credit_requirements.append(CreditRequirement(
            id="liberal", name="교양학점", area="liberal_arts", minimum=resolved.liberal_arts_min, sources=cite(liberal),
        ))

    used_sources = [*cite(raw), *cite(credits["total"]), *cite(credits["major"])]
    for requirement in credit_requirements + course_groups + records + verification:
        used_sources.extend(requirement.sources)
    return GraduationRule(
        id=raw["id"], dataset="official", major=student.major, admission_from=raw["admission_years"][0],
        admission_to=raw["admission_years"][1], allowed_tracks=list(raw.get("tracks", {})) or [None],
        sources=list({item.title: item for item in used_sources}.values()), notes=notes,
        credit_requirements=credit_requirements,
        course_requirements=course_groups, nonacademic_requirements=records, verification_requirements=verification,
        transcript_categories={
            key: CreditTreatment(category=category)
            for key, category in {
                "교양": "liberal_arts", "교양필수": "liberal_arts", "교양선택": "liberal_arts",
                "일반선택": "general", "다중전공": "general", "현장실습": "general",
                "해외대학": "general", "대학원": "general", "융합전공": "general",
            }.items()
        },
        major_transcript_categories=["전공필수", "전공선택", "전공"],
        department_major_codes=sorted(major_codes), course_treatments=treatments,
        prefix_treatments={"FUTR": CreditTreatment(category="general"), "STUP": CreditTreatment(category="general"),
                           "CLTR": CreditTreatment(category="liberal_arts")},
        equivalent_courses=equivalent, review_equivalences=_retake_pairs(), credit_caps=caps,
        transfer_credit_max=resolved.total_credits / 2 if transfer else None,
    )
