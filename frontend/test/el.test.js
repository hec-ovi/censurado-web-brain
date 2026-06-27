import "./setup.js";
import { test } from "node:test";
import assert from "node:assert/strict";
import { screen } from "@testing-library/dom";
import userEvent from "@testing-library/user-event";
import { installDom } from "./dom.js";
import { help } from "../src/components/el.js";

installDom();

test("help() renders a clickable ? marker carrying the text as its accessible name", async () => {
  document.body.appendChild(help("Synthesize an author persona from a seed."));
  const user = userEvent.setup();

  const marker = screen.getByRole("button", { name: /synthesize an author persona/i });
  assert.equal(marker.textContent, "?");
  assert.equal(marker.getAttribute("title"), "Synthesize an author persona from a seed.");
  assert.equal(marker.getAttribute("aria-expanded"), "false");

  await user.click(marker);
  assert.equal(marker.getAttribute("aria-expanded"), "true");
  assert.equal(screen.getByText("Synthesize an author persona from a seed.").hidden, false);
});
