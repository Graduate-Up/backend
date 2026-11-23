// 졸업 진행률 대시보드.
// course 항목은 서버(/api/v1/me/progress)가 이수 과목으로 계산한 값을 그대로 쓰고,
// checklist 항목(TOEIC, 상담 등)은 체크박스 상태로 즉시 진행률을 다시 계산한다.

// app/services/graduation_engine/progress.py 의 overall_percent 와 같은 공식.
export function computeProgress(items) {
  if (!items.length) return 0;
  const fractions = items.map(itemFraction);
  if (items.every(isComplete)) return 100;
  const average = 100 * fractions.reduce((sum, value) => sum + value, 0) / items.length;
  return Math.min(99, Math.round(average * 10) / 10);
}

function isComplete(item) {
  return item.kind === "checklist" ? Boolean(item.checked) : item.status === "satisfied";
}

function itemFraction(item) {
  return item.kind === "checklist" ? (item.checked ? 1 : 0) : item.fraction;
}

function itemStatus(item) {
  if (item.kind === "checklist") return item.checked ? "satisfied" : "unsatisfied";
  return item.status;
}

const STATUS_LABELS = {
  satisfied: "충족", unsatisfied: "미충족", approval_required: "승인 필요", verification_required: "확인 필요",
};

function formatNumber(value) {
  return Number.isInteger(value) ? String(value) : String(Math.round(value * 100) / 100);
}

function describe(item) {
  const unit = item.unit || "";
  if (item.kind === "checklist") {
    if (item.auto_checked) return "자동 확인";
    return !unit && item.required === 1 ? "직접 확인" : "기준 " + formatNumber(item.required) + unit;
  }
  if (item.id === "gpa") {
    const current = item.status === "verification_required" ? "성적 없음" : "평점 " + item.current.toFixed(2);
    return current + " · 기준 " + formatNumber(item.required) + " 이상";
  }
  return formatNumber(item.current) + " / " + formatNumber(item.required) + unit;
}

function counts(items) {
  const result = {unsatisfied: 0, approval_required: 0, verification_required: 0};
  for (const item of items) {
    const state = itemStatus(item);
    if (state in result) result[state] += 1;
  }
  return result;
}

function element(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  node.append(...children);
  return node;
}

// onToggle(item, checked) 는 저장을 담당한다. 실패하면 throw 하고, 체크를 되돌린 뒤 onError 로 알린다.
export function renderDashboard(container, data, onToggle, onError) {
  const items = data.items.map(item => ({...item}));
  container.replaceChildren();

  const percent = element("strong", {className: "dash-percent"});
  const bar = element("div", {className: "dash-bar-fill"});
  const summary = element("span", {className: "dash-summary"});
  const coursePercent = element("p", {className: "dash-note"});

  function refresh() {
    const value = computeProgress(items);
    percent.textContent = formatNumber(value) + "%";
    bar.style.width = value + "%";
    const {unsatisfied, approval_required, verification_required} = counts(items);
    const parts = ["미충족 " + unsatisfied];
    if (approval_required) parts.push("승인 필요 " + approval_required);
    if (verification_required) parts.push("확인 필요 " + verification_required);
    summary.textContent = parts.join(" · ");
    coursePercent.textContent = "이수 과목 기준 " + formatNumber(data.course_progress) +
      "% · 요건별 충족률의 평균이에요. 모든 요건을 충족해야 100%가 돼요.";
  }

  const head = element("div", {className: "dash-head"}, element("span", {}, "전체 진행률"), summary);
  const progressCard = element("section", {className: "dash-card"},
    head, percent, element("div", {className: "dash-bar"}, bar), coursePercent);

  const list = element("ul", {className: "dash-list"});
  for (const item of items) {
    const badge = element("span", {className: "dash-badge"});
    const paint = () => {
      const state = itemStatus(item);
      badge.textContent = STATUS_LABELS[state];
      badge.dataset.state = state;
    };
    paint();

    let label;
    if (item.kind === "checklist") {
      // 이수 과목으로 충족이 확인된 항목은 학생이 바꿀 수 없다.
      const box = element("input", {type: "checkbox", checked: Boolean(item.checked), disabled: item.auto_checked});
      box.addEventListener("change", async () => {
        const next = box.checked;
        item.checked = next;
        paint();
        refresh();
        box.disabled = true;
        try {
          await onToggle(item, next);
        } catch (error) {
          item.checked = !next;
          box.checked = !next;
          paint();
          refresh();
          onError?.(error);
        } finally {
          box.disabled = Boolean(item.auto_checked);
        }
      });
      label = element("label", {className: "dash-name"}, box, " " + item.name);
      if (item.auto_checked) label.append(element("small", {className: "dash-auto"}, "이수 과목으로 확인"));
    } else {
      label = element("span", {className: "dash-name"}, item.name);
    }
    const row = element("li", {className: "dash-row", title: item.reason},
      label, element("span", {className: "dash-value"}, describe(item)), badge);
    if (item.details.length) row.append(element("small", {className: "dash-details"}, item.details.join(" · ")));
    list.append(row);
  }

  const listCard = element("section", {className: "dash-card"},
    element("div", {className: "dash-head"}, element("span", {}, "요건별 상태"),
      element("span", {className: "dash-summary"}, "비교과 항목은 체크하면 바로 반영돼요")),
    list);

  container.append(progressCard, listCard);
  if (data.notices.length) {
    container.append(element("p", {className: "dash-note"},
      "규칙 해석 확인 필요 " + data.notices.length + "건은 진행률에 포함하지 않았어요. 최종 졸업사정은 학부 사무실 확인이 필요해요."));
  }
  refresh();
}
