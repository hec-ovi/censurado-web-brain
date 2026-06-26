import "./setup.js";
import { test } from "node:test";
import assert from "node:assert/strict";
import { screen } from "@testing-library/dom";
import { installDom } from "./dom.js";
import { help } from "../src/components/el.js";

installDom();

test("help() renders a focusable ? marker carrying the text as its accessible name", () => {
  document.body.appendChild(help("Synthesize an author persona from a seed."));

  // role=img + aria-label means it is queryable by its accessible name.
  const marker = screen.getByRole("img", { name: /synthesize an author persona/i });
  assert.equal(marker.textContent, "?");
  assert.equal(marker.getAttribute("title"), "Synthesize an author persona from a seed.");
  // Keyboard-focusable so the tooltip is reachable without a mouse.
  assert.equal(marker.getAttribute("tabindex"), "0");
});
