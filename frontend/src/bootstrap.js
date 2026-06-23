import { mountApp } from "./main.js";

// Browser entry point, kept as an external module (not inline in index.html) so
// the page can be served under a strict Content-Security-Policy with
// script-src 'self' and no 'unsafe-inline'.
const root = document.getElementById("app");
if (root) mountApp(root);
