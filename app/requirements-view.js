function appendText(parent, tag, value) {
  const element = document.createElement(tag);
  element.textContent = value;
  parent.append(element);
  return element;
}

function addList(result, title, items) {
  if (!items.length) return;
  const section = document.createElement("section");
  appendText(section, "h3", title);
  const list = document.createElement("ul");
  for (const item of items) appendText(list, "li", item);
  section.append(list);
  result.append(section);
}

function formatOtherRequirement(item) {
  const {id, name, minimum} = item;
  if (item.unit) return name + " " + minimum + item.unit + " 이상";
  if (id === "counseling") return name + " " + minimum + "회 이상";
  if (id === "gpa") return name + " " + minimum + " 이상";
  if (id === "field-practice") return "현장실습 " + minimum + "학점 이상 또는 승인된 대체 실적";
  if (id === "sdg" || name.startsWith("교양 영역:")) return name + " " + minimum + "학점 이상";
  if (id === "practical-english" || id === "software") return name + " 충족";
  return minimum === 1 ? name : name + " · 기준값 " + minimum;
}

export function renderRequirements(result, data, {admissionYear, majorName, term, trackName}) {
  result.replaceChildren();
  const heading = appendText(result, "h2", admissionYear + "학번 " + majorName);
  heading.id = "result-title";
  if (term) {
    const [year, month] = term.split("-");
    appendText(result, "p", "졸업 예정 " + year + "년 " + Number(month) + "월");
  }
  if (data.dataset === "test") appendText(result, "p", "가상 테스트 규칙");
  if (trackName) appendText(result, "p", trackName);

  addList(result, "학점 기준", [
    ...data.credit_requirements.map(item => item.name + " " + item.minimum + "학점 이상"),
    ...data.credit_caps.map(cap => {
      const areas = { major: "전공", liberal_arts: "교양", general: "일반선택" };
      const scope = [areas[cap.category], cap.code_prefix && cap.code_prefix + " 과목",
        cap.course_codes?.join(", ")].filter(Boolean);
      return scope.join(" · ") + " 최대 " + cap.maximum + "학점 인정";
    }),
  ]);
  for (const group of data.course_requirements) {
    const count = group.choose === group.courses.length
      ? group.choose + "과목 모두 이수" : group.courses.length + "과목 중 " + group.choose + "과목 이수";
    const courses = group.courses.map(course => {
      const same = data.equivalent_courses[course.course_code] || [];
      const notes = [];
      if (same.length) notes.push("동일과목 " + same.join(", "));
      if (course.substitutes.length) notes.push("대체과목 " + course.substitutes.join(", "));
      return course.course_name + " (" + course.course_code + ")" +
        (notes.length ? " · " + notes.join(" · ") : "");
    });
    if (group.require_department) courses.push("컴퓨터학부 개설 과목만 인정");
    if (group.required_transcript_category) {
      courses.push("성적표 교과구분은 " + group.required_transcript_category + "이어야 함");
    }
    addList(result, group.name + " · " + count, courses);
  }
  addList(result, "기타 졸업요건", data.nonacademic_requirements.flatMap(item => [
    formatOtherRequirement(item), ...(item.details || []).map(detail => "↳ " + detail),
  ]));
  addList(result, "적용 안내", data.notes || []);
  addList(result, "추가 확인이 필요한 항목", data.verification_requirements.map(item => item.name));

  if (data.sources.length) {
    const details = document.createElement("details");
    appendText(details, "summary", "규정 출처");
    const list = document.createElement("ul");
    for (const source of data.sources) {
      const item = document.createElement("li");
      if (source.url && /^https?:\/\//i.test(source.url)) {
        const link = appendText(item, "a", source.title);
        link.href = source.url;
      } else {
        item.textContent = source.title;
      }
      if (source.reference) item.append(" (" + source.reference + ")");
      list.append(item);
    }
    details.append(list);
    result.append(details);
  }
  result.hidden = false;
}
