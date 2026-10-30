"""侧栏双面板浏览器组件（Streamlit Custom Components v2，内联实现）。

固定总高挂载（**勿用 stretch**：侧栏中会塌成 0 导致组件不可见，实测踩过）。
上面板=数据文件（面包屑+目录/.spe 紧凑行），下面板=材料（紧凑行）。
- 单击 .spe = 选中高亮（延迟 260ms，可被双击取消）；双击 = 加载（目录双击=进入；
  材料 double-click = 插入堆叠）。点击时序存在持久的根元素上，DOM 重建不丢双击。
- 右键菜单：数据行（进入/加载/拷贝/粘贴/重命名/删除/上一级）、材料行（插入/查看 n·k/
  重命名/删除）、空白（粘贴/上一级）；快捷键 ⌘C/⌘V/F2/Delete/Backspace。
- 面板自带边框、标题内置；中缝分割条可拖动调两栏比例（组件 state `ratio` 持久化）。
事件经 setTriggerValue("event", {type, path}) 回传 Python。
"""
import streamlit as st

_HTML = """
<div class="cb">
  <div class="cb-crumb"></div>
  <div class="cb-body">
    <div class="cb-panel cb-top">
      <div class="cb-phead">数据</div>
      <div class="cb-list cb-dlist" tabindex="0"></div>
    </div>
    <div class="cb-div"></div>
    <div class="cb-panel cb-bottom">
      <div class="cb-phead">材料</div>
      <div class="cb-list cb-mlist"></div>
    </div>
  </div>
  <div class="cb-menu"></div>
  <div class="cb-status"></div>
</div>
"""

_CSS = """
.cb { position: relative; display: flex; flex-direction: column; height: 520px;
      font-size: 12px; color: var(--st-text-color, #1c2733); }
.cb-crumb { flex: 0 0 auto; display: flex; align-items: center; gap: 2px; padding: 4px 6px;
            border-bottom: 1px solid var(--st-border-color, #d0d5dd); }
.cb-path { flex: 1 1 auto; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cb-seg { cursor: pointer; color: var(--st-primary-color, #2b6cb0); }
.cb-seg:hover { text-decoration: underline; }
.cb-sep { margin: 0 3px; opacity: 0.45; }
.cb-up { flex: 0 0 auto; cursor: pointer; color: var(--st-primary-color, #2b6cb0);
         margin-left: 8px; font-weight: 700; }
.cb-up:hover { text-decoration: underline; }
.cb-off { opacity: 0.35; cursor: default; }
.cb-sep { margin: 0 3px; opacity: 0.45; }
.cb-body { flex: 1 1 auto; display: flex; flex-direction: column; min-height: 0; }
.cb-panel { display: flex; flex-direction: column; min-height: 0; overflow-y: auto;
            border: 1px solid var(--st-border-color, #d0d5dd); border-radius: 6px; }
.cb-top { flex: 0 0 55%; }
.cb-bottom { flex: 1 1 auto; }
.cb-div { flex: 0 0 6px; cursor: row-resize; margin: 2px 0; }
.cb-div::before { content: ""; display: block; height: 2px; margin: 2px 6px;
                  background: var(--st-border-color, #c8cdd6); border-radius: 1px; }
.cb-div:hover::before { background: var(--st-primary-color, #2b6cb0); }
.cb-phead { position: sticky; top: 0; z-index: 2; background: var(--st-background-color, #fff);
            font-weight: 600; padding: 3px 6px; border-bottom: 1px solid var(--st-border-color, #e4e8ee); }
.cb-list { flex: 1 1 auto; outline: none; padding: 2px 0; }
.cb-row { display: flex; align-items: center; gap: 6px; padding: 2px 6px; line-height: 20px;
          white-space: nowrap; border-radius: 4px; }
.cb-row:hover { background: var(--st-secondary-background-color, #f0f2f5); }
.cb-sel { background: var(--st-primary-color, #2b6cb0); color: #fff; }
.cb-sel:hover { background: var(--st-primary-color, #2b6cb0); }
.cb-loaded { font-weight: 700; }
.cb-unsafe { opacity: 0.55; }
.cb-ic { flex: 0 0 auto; }
.cb-nm { overflow: hidden; text-overflow: ellipsis; }
.cb-empty { padding: 8px; opacity: 0.6; }
.cb-menu { display: none; position: absolute; background: var(--st-background-color, #fff);
           border: 1px solid var(--st-border-color, #d0d5dd); border-radius: 6px;
           box-shadow: 0 4px 14px rgba(0,0,0,0.15); min-width: 170px; z-index: 20; padding: 4px 0; }
.cb-mi { padding: 5px 12px; cursor: pointer; }
.cb-mi:hover { background: var(--st-secondary-background-color, #f0f2f5); }
.cb-dis { opacity: 0.45; }
.cb-hr { border-top: 1px solid var(--st-border-color, #e8edf2); margin: 4px 0; }
.cb-status { flex: 0 0 auto; border-top: 1px solid var(--st-border-color, #d0d5dd);
             padding: 4px 6px; opacity: 0.7; white-space: nowrap; overflow: hidden;
             text-overflow: ellipsis; }
.cb-crumb:empty, .cb-status:empty { display: none; }
"""

_JS = """
export default function (component) {
  const { data, parentElement, setStateValue, setTriggerValue } = component
  const root = parentElement.querySelector(".cb")
  if (!root) return
  const crumbEl = root.querySelector(".cb-crumb")
  const topEl = root.querySelector(".cb-top")
  const dlistEl = root.querySelector(".cb-dlist")
  const mlistEl = root.querySelector(".cb-mlist")
  const menuEl = root.querySelector(".cb-menu")
  const statusEl = root.querySelector(".cb-status")
  const divEl = root.querySelector(".cb-div")

  const fire = (type, path) => setTriggerValue("event", { type: type, path: path || "" })
  const clipName = (data && data.clip) || ""

  const L = { enter: "\\u8fdb\\u5165", open: "\\u52a0\\u8f7d", copy: "\\u62f7\\u8d1d",
              ren: "\\u91cd\\u547d\\u540d", del: "\\u5220\\u9664\\uff08\\u56de\\u6536\\u7ad9\\uff09",
              paste: "\\u7c98\\u8d34\\u5230\\u5f53\\u524d\\u76ee\\u5f55", paste0: "\\u7c98\\u8d34\\uff08\\u526a\\u8d34\\u677f\\u7a7a\\uff09",
              up: "\\u4e0a\\u4e00\\u7ea7", ins: "\\u63d2\\u5165\\u5806\\u53e0", view: "\\u67e5\\u770b n\\u00b7k",
              emptyDir: "\\uff08\\u7a7a\\u76ee\\u5f55\\uff09",
              emptyMat: "\\uff08\\u7a7a\\uff09",
              sel: "\\u5df2\\u9009: ", loaded: "\\u5df2\\u52a0\\u8f7d: ", clip: "\\u526a\\u8d34\\u677f: " }

  function hideMenu() { menuEl.style.display = "none" }
  function showMenu(e, items) {
    hideMenu()
    menuEl.innerHTML = ""
    items.forEach(it => {
      if (it[0] === "---") {
        const hr = document.createElement("div")
        hr.className = "cb-hr"
        menuEl.appendChild(hr)
        return
      }
      const mi = document.createElement("div")
      mi.className = "cb-mi" + (it[2] ? " cb-dis" : "")
      mi.textContent = it[0]
      mi.onclick = () => { hideMenu(); if (!it[2]) it[1]() }
      menuEl.appendChild(mi)
    })
    menuEl.style.display = "block"
    const rect = root.getBoundingClientRect()
    menuEl.style.left = Math.max(0, Math.min(e.clientX - rect.left, rect.width - 180)) + "px"
    menuEl.style.top = Math.max(0, Math.min(e.clientY - rect.top, rect.height - 26 * items.length)) + "px"
  }

  function rowEl(icon, text, title, cls) {
    const row = document.createElement("div")
    row.className = "cb-row" + (cls ? " " + cls : "")
    const ic = document.createElement("span")
    ic.className = "cb-ic"
    ic.textContent = icon
    const nm = document.createElement("span")
    nm.className = "cb-nm"
    nm.textContent = text
    nm.title = title
    row.appendChild(ic)
    row.appendChild(nm)
    return row
  }

  // ---- 点击时序：单击(260ms 延迟、可取消) / 400ms 内二击 = 双击；状态存持久根元素 ----
  if (!root._clicks) root._clicks = {}
  const clicks = root._clicks

  function speClick(en) {
    const now = Date.now()
    const last = clicks[en.path]
    if (last && now - last.t < 400) {
      delete clicks[en.path]
      if (last.timer) clearTimeout(last.timer)
      fire("open", en.path)
    } else {
      if (last && last.timer) clearTimeout(last.timer)
      const stamp = { t: now, timer: null }
      stamp.timer = setTimeout(() => {
        if (clicks[en.path] === stamp) {
          delete clicks[en.path]
          fire("select", en.path)
        }
      }, 260)
      clicks[en.path] = stamp
    }
  }

  // ---- 上面板：数据文件 ----
  const entries = (data && data.entries) || []
  const crumb = (data && data.crumb) || []
  const selName = (data && data.selected) || ""
  const loadedName = (data && data.loaded) || ""

  // ---- 固定高度（JS 直接设根元素像素高，内部滚动；挂载 height 参数对组件无效会随内容增长）----
  const hpx = (data && data.height_px) || 520
  root.style.height = hpx + "px"

  // ---- 面包屑（路径左、⬆ 右）----
  crumbEl.innerHTML = ""
  const pathWrap = document.createElement("span")
  pathWrap.className = "cb-path"
  crumb.forEach((seg, i) => {
    if (i > 0) {
      const sep = document.createElement("span")
      sep.className = "cb-sep"
      sep.textContent = "\\u203a"
      pathWrap.appendChild(sep)
    }
    const b = document.createElement("span")
    b.className = "cb-seg"
    b.textContent = seg.name
    b.title = seg.path
    b.onclick = () => fire("enter", seg.path)
    pathWrap.appendChild(b)
  })
  crumbEl.appendChild(pathWrap)
  const up = document.createElement("span")
  up.className = "cb-up" + (crumb.length > 1 ? "" : " cb-off")
  up.textContent = "\\u2b06"
  up.title = "\\u4e0a\\u4e00\\u7ea7"
  if (crumb.length > 1) up.onclick = () => fire("up", "")
  crumbEl.appendChild(up)

  dlistEl.innerHTML = ""
  entries.forEach(en => {
    const cls = (en.selected ? "cb-sel " : "") + (en.safe ? "" : "cb-unsafe ") + (en.loaded ? "cb-loaded" : "")
    const row = rowEl((en.kind === "dir" ? "\\ud83d\\udcc1" : "\\ud83d\\udcc4") + (en.loaded ? " \\u25cf" : ""),
                      en.name, en.path, cls.trim())
    if (en.kind === "spe") {
      row.onclick = () => speClick(en)
    } else {
      row.ondblclick = () => fire("enter", en.path)
    }
    row.oncontextmenu = (e) => {
      e.preventDefault(); e.stopPropagation()
      const items = []
      if (en.kind === "dir") items.push([L.enter, () => fire("enter", en.path)])
      else items.push([L.open, () => fire("open", en.path)])
      if (en.safe) {
        items.push([L.copy + "  \\u2318C", () => fire("copy", en.path)])
        items.push([L.ren + "  F2", () => fire("rename", en.path)])
        items.push([L.del + "  Del", () => fire("delete", en.path)])
      }
      items.push(["---"])
      items.push([clipName ? L.paste + "  \\u2318V" : L.paste0, () => fire("paste", ""), !clipName])
      items.push([L.up + "  \\u232b", () => fire("up", "")])
      showMenu(e, items)
    }
    dlistEl.appendChild(row)
  })
  if (!entries.length) {
    const empty = document.createElement("div")
    empty.className = "cb-empty"
    empty.textContent = L.emptyDir
    dlistEl.appendChild(empty)
  }

  statusEl.textContent =
    (selName ? L.sel + selName : "") +
    (loadedName ? (selName ? "  " : "") + L.loaded + loadedName : "") +
    (clipName ? "  " + L.clip + clipName : "")

  dlistEl.tabIndex = 0
  dlistEl.onkeydown = (e) => {
    const sel = entries.find(en => en.selected)
    const mod = e.metaKey || e.ctrlKey
    if (mod && (e.key === "c" || e.key === "C")) {
      if (sel && sel.safe) { e.preventDefault(); fire("copy", sel.path) }
    } else if (mod && (e.key === "v" || e.key === "V")) {
      e.preventDefault(); fire("paste", "")
    } else if (e.key === "F2") {
      if (sel && sel.safe) { e.preventDefault(); fire("rename", sel.path) }
    } else if (e.key === "Delete") {
      if (sel && sel.safe) { e.preventDefault(); fire("delete", sel.path) }
    } else if (e.key === "Backspace") {
      e.preventDefault(); fire("up", "")
    }
  }

  // ---- 下面板：材料 ----
  const materials = (data && data.materials) || []
  mlistEl.innerHTML = ""
  materials.forEach(m => {
    const row = rowEl("\\ud83d\\udcc4", m.label, m.name, "")
    row.ondblclick = () => fire("mat_insert", m.name)
    row.oncontextmenu = (e) => {
      e.preventDefault(); e.stopPropagation()
      showMenu(e, [
        [L.ins, () => fire("mat_insert", m.name)],
        [L.view, () => fire("mat_view", m.name)],
        [L.ren, () => fire("mat_rename", m.name)],
        [L.del, () => fire("mat_delete", m.name)]
      ])
    }
    mlistEl.appendChild(row)
  })
  if (!materials.length) {
    const empty = document.createElement("div")
    empty.className = "cb-empty"
    empty.textContent = L.emptyMat
    mlistEl.appendChild(empty)
  }

  root.onclick = () => hideMenu()
  root.oncontextmenu = (e) => {
    e.preventDefault()
    showMenu(e, [
      [clipName ? L.paste + "  \\u2318V" : L.paste0, () => fire("paste", ""), !clipName],
      [L.up + "  \\u232b", () => fire("up", "")]
    ])
  }

  // ---- 分割条拖动：两栏比例实时调整，松手后经 state 持久化 ----
  let ratio = (data && typeof data.ratio === "number") ? data.ratio : 0.55
  topEl.style.flex = "0 0 " + (ratio * 100) + "%"
  let curRatio = ratio
  divEl.onmousedown = (e) => {
    e.preventDefault()
    const body = root.querySelector(".cb-body")
    const rect = body.getBoundingClientRect()
    const move = (ev) => {
      let r = (ev.clientY - rect.top) / rect.height
      r = Math.max(0.15, Math.min(0.85, r))
      curRatio = r
      topEl.style.flex = "0 0 " + (r * 100) + "%"
    }
    const up = () => {
      document.removeEventListener("mousemove", move)
      document.removeEventListener("mouseup", up)
      setStateValue("ratio", curRatio)
    }
    document.addEventListener("mousemove", move)
    document.addEventListener("mouseup", up)
  }

  // ---- 视口高度自适应：量父页高度回传，Python 按其 50% 重挂载（仅变化时回传防循环）----
  try {
    const vh = (window.parent && window.parent.innerHeight) || window.innerHeight
    if (Math.abs(((data && data.vh) || 0) - vh) > 2) setStateValue("vh", vh)
  } catch (err) { /* 跨域等异常时保持默认高度 */ }

  return () => {}
}
"""

_BROWSER = st.components.v2.component("compact_browser", html=_HTML, css=_CSS, js=_JS)


def dual_explorer(*, crumb, entries, materials, selected_name="", loaded_name="",
                  clip_name="", on_event=None, key="fx", default_height=800):
    """双面板浏览器。总高 = 整屏（JS 量 parent.innerHeight 回传 `vh`，Python 以
    `vh − 64`（头部余量）重挂载；下限 400px；首帧用 default_height）。高度勿用 stretch。"""
    def _cb():
        state = st.session_state.get(key)
        ev = state.get("event") if isinstance(state, dict) else getattr(state, "event", None)
        if ev and on_event is not None:
            on_event(ev)

    state = st.session_state.get(key)
    ratio = state.get("ratio") if isinstance(state, dict) else getattr(state, "ratio", None)
    if not isinstance(ratio, (int, float)):
        ratio = 0.55
    vh = state.get("vh") if isinstance(state, dict) else getattr(state, "vh", None)
    # 占满整屏：视口高 − 侧栏头部余量（"🗂 Explorer" 标题 + 呼吸空间），下限防极端小窗
    height = int(max(400, round(vh) - 64)) if isinstance(vh, (int, float)) else default_height

    return _BROWSER(
        key=key,
        data={"crumb": crumb, "entries": entries, "materials": materials,
              "selected": selected_name, "loaded": loaded_name,
              "clip": clip_name, "ratio": float(ratio),
              "vh": int(vh) if isinstance(vh, (int, float)) else 0,
              "height_px": height},
        on_event_change=_cb,
    )
