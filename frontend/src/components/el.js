// A tiny DOM builder so the components stay buildless and dependency-free.
// el("div", { class: "x", onClick: fn }, [child, "text"]) -> HTMLElement.

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);

  for (const [key, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key === "html") node.innerHTML = value;
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (value === true) node.setAttribute(key, "");
    else node.setAttribute(key, value);
  }

  for (const child of [].concat(children)) {
    if (child == null || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

// A labeled control: an explicit <label for=id> next to the control, so the
// accessible name is set and tests can query by role/label.
export function field(labelText, control, id) {
  return el("div", { class: "field" }, [el("label", { for: id }, labelText), control]);
}

// A small inline (?) marker that carries an explanation for the control next to
// it. Both `title` (hover tooltip) and `aria-label` (screen readers + tests)
// carry the text, and tabindex makes it keyboard-focusable so the tooltip is
// reachable without a mouse. Used across sections so every control is
// self-explaining.
export function help(text) {
  return el("span", { class: "help", role: "img", tabindex: "0", "aria-label": text, title: text }, "?");
}

// Only render an image src we trust: a same-origin path or an explicit http(s)
// or data:image URL. Anything else (a javascript: or other odd scheme) is
// rejected so the caller can fall back. Shared by the persona avatar and the
// run hero-image preview.
export function isSafeImageSrc(value) {
  if (!value || typeof value !== "string") return false;
  return value.startsWith("/") || /^https?:\/\//i.test(value) || /^data:image\//i.test(value);
}
