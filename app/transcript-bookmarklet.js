(async function importVisibleTranscript(jolupOrigin) {
  if (!["knuin.knu.ac.kr", "yes.knu.ac.kr"].includes(location.hostname)) {
    alert("경북대학교 통합정보시스템의 이수성적조회 화면에서 실행해 주세요.");
    return;
  }
  const codePattern = /^[A-Z]{4}[0-9]{4}$/i;
  const pass = /^(?:[ABCD][+0-]?|P|S|PASS|합격|이수)$/i;
  const fail = /^(?:F|U|NP|FAIL|불합격|미이수)$/i;
  const docs = [document];
  for (const doc of docs) {
    for (const frame of doc.querySelectorAll("iframe, frame")) {
      try {
        if (frame.contentDocument && !docs.includes(frame.contentDocument)) docs.push(frame.contentDocument);
      } catch {
        // Frames on another origin are not accessible from this page.
      }
    }
  }

  function visibleRows(root) {
    return [...root.querySelectorAll('tr, [role="row"]')].map(row =>
      [...row.querySelectorAll('th, td, [role="columnheader"], [role="gridcell"], [role="cell"]')]
        .map(cell => (cell.innerText || cell.textContent || "").trim()));
  }

  function webSquareGrid(root, doc) {
    const view = doc.defaultView;
    const ids = new Set();
    for (let element = root; element; element = element.parentElement) {
      if (!element.id) continue;
      ids.add(element.id);
      const match = element.id.match(/^(.+?)_(?:main|body|head|grid|data|scroll|table)(?:_|$)/i);
      if (match) ids.add(match[1]);
      if (ids.size >= 20) break;
    }
    for (const id of ids) {
      for (const namespace of [view?.WebSquare?.util, view?.$p]) {
        try {
          const component = namespace?.getComponentById?.(id);
          if (typeof component?.setTopRowIndex === "function" &&
              typeof component.getTopRowIndex === "function") return component;
        } catch {
          // A grid subelement is not necessarily a WebSquare component.
        }
      }
    }
    return null;
  }

  const tables = docs.flatMap(doc => [...doc.querySelectorAll('table, [role="grid"], [role="table"]')]
    .map(root => ({root, doc}))
    .filter(({root}) => root.getClientRects().length > 0));
  const headers = ["년도", "학기", "교과목구분", "교과목코드", "교과목명", "학점", "성적등급"];
  const candidates = tables.map(({root, doc}) => {
    const heading = (doc.body?.innerText || "").match(/전체\s*성적\s*([\d,]+)\s*건/);
    const rows = visibleRows(root);
    const header = rows.find(row => row.includes("교과목코드") && row.includes("학점"));
    if (!header) return null;
    const indexes = headers.map(label => header.indexOf(label));
    if (indexes[0] < 0) indexes[0] = header.indexOf("연도");
    const read = () => visibleRows((root.id && doc.getElementById(root.id)) || root)
      .map(row => Object.fromEntries(headers.map((label, i) => [label, row[indexes[i]] || ""])))
      .filter(row => codePattern.test(row["교과목코드"]));
    const expected = heading ? Number(heading[1].replace(/,/g, "")) : null;
    return {root, doc, data: read(), expected, read};
  }).filter(Boolean).sort((a, b) => b.data.length - a.data.length);

  const found = candidates[0];
  if (!found?.data.length) {
    alert("읽을 수 있는 전체성적 표를 찾지 못했습니다. 표가 화면에 열린 상태인지 확인해 주세요.");
    return;
  }
  const initialRows = found.data.length;
  let scrollTargets = 0;
  let wheelAttempts = 0;
  let gridMoves = 0;
  const popup = window.open(jolupOrigin + "/account?transcript=1", "jolup-transcript");
  if (!popup) {
    alert("졸UP 창이 차단되었습니다. 팝업을 허용한 다음 다시 누르세요.");
    return;
  }
  const ready = new Promise(resolve => {
    const expire = setTimeout(() => finish(false), 30000);
    function finish(value) {
      clearTimeout(expire);
      window.removeEventListener("message", onReady);
      resolve(value);
    }
    function onReady(event) {
      if (event.origin === jolupOrigin && event.source === popup && event.data === "jolup-transcript-ready") {
        finish(true);
      }
    }
    window.addEventListener("message", onReady);
  });

  if (found.expected === null || found.data.length < found.expected) {
    const scrollAreas = [found.root, ...found.root.querySelectorAll("*")];
    for (let parent = found.root.parentElement; parent; parent = parent.parentElement) scrollAreas.push(parent);
    const vicinity = found.root.parentElement?.parentElement;
    if (vicinity) scrollAreas.push(...vicinity.querySelectorAll("div, [role=scrollbar], [role=grid]"));
    const scrollers = [...new Set(scrollAreas)].filter(element => element.clientHeight > 0 &&
      element.scrollHeight > element.clientHeight + 2 && element.getClientRects().length > 0 &&
      /auto|scroll|hidden|overlay/.test(element.ownerDocument.defaultView.getComputedStyle(element).overflowY));
    scrollTargets = scrollers.length;
    const seen = new Map(found.data.map(row => [JSON.stringify(row), row]));
    const deadline = Date.now() + 20000;
    const enough = () => found.expected !== null && seen.size >= found.expected;
    const wait = delay => new Promise(resolve => setTimeout(resolve, delay));
    const capture = () => { for (const row of found.read()) seen.set(JSON.stringify(row), row); };
    const grid = webSquareGrid(found.root, found.doc);
    if (grid && Number.isInteger(found.expected) && found.expected <= 500) {
      const step = Math.max(1, Math.floor(initialRows / 2));
      let top;
      try {
        top = grid.getTopRowIndex();
        for (let index = 0; index < found.expected && !enough(); index += step) {
          grid.setTopRowIndex(index, true, false);
          gridMoves++;
          await wait(70);
          capture();
        }
      } catch {
        // Fall back to the rendered table if this component cannot move by row index.
      } finally {
        if (Number.isInteger(top) && gridMoves) {
          try { grid.setTopRowIndex(top, true, false); } catch {}
        }
      }
    }
    for (const scroller of scrollers) {
      if (enough() || Date.now() > deadline) break;
      const previous = scroller.scrollTop;
      try {
        const step = Math.max(1, scroller.clientHeight * 0.55);
        let moved = false;
        for (let i = 0; i < 140 && !enough() && Date.now() <= deadline; i++) {
          const end = scroller.scrollHeight - scroller.clientHeight;
          const position = Math.min(end, i * step);
          scroller.scrollTop = position;
          await wait(180);
          if (i && scroller.scrollTop > 0) moved = true;
          capture();
          if (position === end || (i >= 2 && !moved)) break;
        }
      } finally {
        scroller.scrollTop = previous;
        await wait(100);
      }
    }
    // Some virtual grids handle wheel events on the rows and keep their offset outside scrollTop.
    if (!enough() && found.expected !== null) {
      const view = found.doc.defaultView;
      if (view?.WheelEvent) {
        const viewport = found.root.querySelector("tbody, [role=rowgroup]") || found.root;
        let unchanged = 0;
        for (let i = 0; i < 100 && !enough() && unchanged < 8 && Date.now() <= deadline + 12000; i++) {
          const before = seen.size;
          const target = viewport.querySelector('td, [role="gridcell"]') || viewport;
          target.dispatchEvent(new view.WheelEvent("wheel", {
            bubbles: true, cancelable: true, composed: true, deltaY: 72, deltaMode: 0,
          }));
          wheelAttempts++;
          await wait(180);
          capture();
          unchanged = before === seen.size ? unchanged + 1 : 0;
        }
      }
    }
    found.data = [...seen.values()];
  }

  const payload = {
    source: "jolup-visible-transcript",
    scanned: found.data.length,
    expected: found.expected,
    diagnostics: {initialRows, scrollTargets, wheelAttempts, gridMoves},
    data: found.data.map(row => {
      const {성적등급: grade, ...course} = row;
      return {...course, 성적등급: pass.test(grade) ? "PASS" : fail.test(grade) ? "FAIL" : ""};
    }),
  };
  if (await ready) {
    popup.postMessage(payload, jolupOrigin);
  } else {
    alert("졸UP 창이 응답하지 않았습니다. 주소가 열리는지 확인해 주세요.");
  }
})
