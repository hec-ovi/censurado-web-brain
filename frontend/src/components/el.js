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
