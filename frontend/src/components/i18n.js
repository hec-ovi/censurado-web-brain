// Buildless i18n for the cockpit. English source strings are the catalog KEYS;
// Spanish is an overlay. The served default is "es"; tests pin "en" in
// test/setup.js so every existing English assertion holds (t() is identity
// under en). The locale is read LIVE on each t() call so a re-mount after a
// language switch picks up the new value and tests stay deterministic.
//
// Catalog completeness is the invariant: every key passed to t() MUST have an
// entry in ES (an entry MAY equal the key for intentional passthroughs like
// "Editorial", "UTC", "Lat/long", proper nouns), so a missing translation is a
// detectable bug, never a silent English leak. Behavior-bearing tokens (DOM
// ids/classes, dataset/data-state values, API JSON keys, enum values sent to the
// server, {{TOKEN}} markers, URLs/paths) are NEVER keys here: only display text.

const STORAGE_KEY = "admin-lang";
const DEFAULT = "es"; // Spanish-first; tests pin "en" in test/setup.js

export const ES = {
  // --- Shell / chrome ---------------------------------------------------
  "Admin Panel": "Panel de administración",
  "Control panel": "Panel de control",
  Theme: "Tema",
  System: "Sistema",
  Light: "Claro",
  Dark: "Oscuro",
  Primary: "Principal",
  "Console sections": "Secciones de la consola",

  // --- Tabs / nav -------------------------------------------------------
  Authors: "Autores",
  Sources: "Fuentes",
  Runs: "Ejecuciones",
  Editorial: "Editorial",
  Prompts: "Prompts",
  Status: "Estado",

  // --- Common verbs / nouns --------------------------------------------
  Save: "Guardar",
  "Save changes": "Guardar cambios",
  Cancel: "Cancelar",
  Delete: "Eliminar",
  Confirm: "Confirmar",
  Edit: "Editar",
  Enable: "Habilitar",
  Disable: "Deshabilitar",
  Enabled: "Habilitada",
  Promote: "Promover",
  Refresh: "Actualizar",
  View: "Ver",
  Personas: "Personas",
  Backend: "Backend",
  Health: "Salud",

  // --- Pill / flag text (dataset.state stays the enum) ------------------
  online: "en línea",
  offline: "fuera de línea",
  checking: "verificando",
  active: "activa",
  inactive: "inactiva",
  yes: "sí",
  no: "no",
  unknown: "desconocido",
  "unknown error": "error desconocido",
  "(unset)": "(sin definir)",

  // --- Persona create form ---------------------------------------------
  "New persona": "Nueva persona",
  "Synthesize an author persona from a short seed. The new author joins the roster below.":
    "Sintetice una persona de autor a partir de una breve semilla. El nuevo autor se suma a la lista de abajo.",
  "Synthesize persona": "Sintetizar persona",
  "Display name": "Nombre visible",
  Beat: "Sección",
  "Seed description": "Descripción semilla",
  "Preferred sources (comma separated)": "Fuentes preferidas (separadas por comas)",
  "Display name and seed are required.": "El nombre visible y la semilla son obligatorios.",
  "Submitting...": "Enviando...",
  'Synthesizing "{id}"...': 'Sintetizando "{id}"...',
  "Created {id}.": "Se creó {id}.",
  "Synthesis failed: {err}.": "La síntesis falló: {err}.",
  ' "{id}"': ' "{id}"',
  "Still synthesizing{which}. This is taking longer than usual; reload later to check.":
    "Sigue sintetizando{which}. Está tardando más de lo habitual; vuelva a cargar más tarde para comprobarlo.",
  "Could not create persona: {msg}": "No se pudo crear la persona: {msg}",

  // --- Persona list ----------------------------------------------------
  ["An author only researches the sources linked here. Cross-source corroboration across them is what " +
    "drives relevance: a claim that several linked outlets carry independently rises, an unsourced one does not. " +
    "Link a few trusted, independent outlets per author."]:
    "Un autor solo investiga las fuentes vinculadas aquí. La corroboración entre fuentes es lo que impulsa la " +
    "relevancia: una afirmación que varios medios vinculados sostienen de forma independiente sube, una sin fuente " +
    "no. Vincule unos pocos medios confiables e independientes por autor.",
  "Filter personas by beat": "Filtrar personas por sección",
  "All beats": "Todas las secciones",
  "Loading personas...": "Cargando personas...",
  "No personas yet. Create one to get started.": "Todavía no hay personas. Cree una para empezar.",
  "Could not load personas: {msg}": "No se pudo cargar las personas: {msg}",
  Language: "Idioma",
  "Avatar path": "Ruta del avatar",
  Active: "Activo",
  "Who I am": "Quién soy",
  About: "Acerca de",
  Style: "Estilo",
  "Positive examples (JSON array)": "Ejemplos positivos (arreglo JSON)",
  "Negative examples (JSON array)": "Ejemplos negativos (arreglo JSON)",
  "Positive examples": "Ejemplos positivos",
  "Negative examples": "Ejemplos negativos",
  "Saving...": "Guardando...",
  "Could not save ({code}): {msg}": "No se pudo guardar ({code}): {msg}",
  "Linked sources": "Fuentes vinculadas",
  "Save sources": "Guardar fuentes",
  "Loading sources...": "Cargando fuentes...",
  "Add sources in the Sources tab first.": "Primero agregue fuentes en la pestaña Fuentes.",
  Source: "Fuente",
  Description: "Descripción",
  "Could not load sources: {msg}": "No se pudo cargar las fuentes: {msg}",
  "Sources updated.": "Fuentes actualizadas.",
  "Could not save sources ({code}): {msg}": "No se pudo guardar las fuentes ({code}): {msg}",
  "Action failed ({code}): {msg}": "La acción falló ({code}): {msg}",
  "{label} must be a valid JSON array.": "{label} debe ser un arreglo JSON válido.",
  "{label} must be a JSON array.": "{label} debe ser un arreglo JSON.",

  // --- Health / backend status -----------------------------------------
  "Live ping of the brain API behind nginx. Green means the API answered.":
    "Ping en vivo de la API del brain detrás de nginx. Verde significa que la API respondió.",
  "Brain API status": "Estado de la API del brain",
  "Checking backend...": "Verificando backend...",
  "Could not load backend status: {msg}": "No se pudo cargar el estado del backend: {msg}",

  // --- Sources panel ----------------------------------------------------
  ["A source (portal) is a news outlet the newsroom may draw from: its domain, homepage, and feeds. " +
    "Add the outlets you trust here; disabled sources stay on file but are skipped."]:
    "Una fuente (portal) es un medio del que la redacción puede nutrirse: su dominio, página principal y feeds. " +
    "Agregue aquí los medios en los que confía; las fuentes deshabilitadas quedan registradas pero se omiten.",
  ["Co-owned mastheads collapse to one independent source for the corroboration gate. Give shared owners the " +
    "same group so two papers under one owner count once, not twice, when a claim needs a second source."]:
    "Las cabeceras de un mismo dueño se cuentan como una sola fuente independiente para el control de corroboración. " +
    "Asigne el mismo grupo a los dueños compartidos para que dos periódicos de un mismo dueño cuenten una vez, no dos, " +
    "cuando una afirmación necesita una segunda fuente.",
  Domain: "Dominio",
  "Ownership group": "Grupo de propiedad",
  Homepage: "Página principal",
  "Feed URLs (comma separated)": "URLs de feeds (separadas por comas)",
  "Feed type": "Tipo de feed",
  "Add source": "Agregar fuente",
  "Domain is required.": "El dominio es obligatorio.",
  "Adding source...": "Agregando fuente...",
  "Added {domain}.": "Se agregó {domain}.",
  "Could not add source ({code}): {msg}": "No se pudo agregar la fuente ({code}): {msg}",
  "No sources yet.": "Todavía no hay fuentes.",
  Portal: "Portal",
  Actions: "Acciones",
  "Assigned to": "Asignado a",
  "No authors assigned": "Sin autores asignados",

  // --- Run panel --------------------------------------------------------
  ["Full manager: the manager assigns every active author and runs the whole newsroom. " +
    "Leave the count blank to let the manager decide how many pieces to commission."]:
    "Redacción completa: el manager asigna a cada autor activo y ejecuta toda la redacción. " +
    "Deje la cantidad en blanco para que el manager decida cuántas piezas encargar.",
  ["Author batch: scope the manager to the authors you tick. Express runs a smaller, quicker batch; " +
    "managed and manual run the full manager logic over just those authors."]:
    "Lote de autores: acote el manager a los autores que marque. Express ejecuta un lote más pequeño y rápido; " +
    "managed y manual ejecutan toda la lógica del manager solo sobre esos autores.",
  ["Single article: the direct path. Write one piece from a brief and/or a specific link with the chosen " +
    "author. This bypasses the manager and the cross-source corroboration floor, so use it for a one-off."]:
    "Artículo único: la vía directa. Escriba una pieza a partir de una consigna o un enlace específico con el " +
    "autor elegido. Esto omite el manager y el piso de corroboración entre fuentes, así que úselo para casos puntuales.",
  "Runs the whole newsroom: the manager assigns every active author.":
    "Ejecuta toda la redacción: el manager asigna a cada autor activo.",
  "Scopes the manager to the authors you tick.": "Acota el manager a los autores que marque.",
  "Writes one piece directly, bypassing the manager.": "Escribe una pieza directamente, omitiendo el manager.",
  "Full manager": "Redacción completa",
  "Author batch": "Lote de autores",
  "Single article": "Artículo único",
  "Start run": "Iniciar ejecución",
  Trigger: "Tipo de ejecución",
  "Trigger a run": "Lanzar una ejecución",
  ["Three ways to commission articles: full manager runs the whole newsroom, author batch scopes the " +
    "manager to the authors you pick, single article writes one piece directly from a link."]:
    "Tres formas de encargar artículos: redacción completa ejecuta toda la redacción, lote de autores acota el " +
    "manager a los autores que elija, artículo único escribe una pieza directamente desde un enlace.",
  "Filter runs by status": "Filtrar ejecuciones por estado",
  "All statuses": "Todos los estados",
  "Run history": "Historial de ejecuciones",
  "Count (n)": "Cantidad (n)",
  default: "predeterminado",
  "Auto-generate images": "Generar imágenes automáticamente",
  "Loading authors...": "Cargando autores...",
  "Could not load authors: {msg}": "No se pudo cargar los autores: {msg}",
  "No authors yet. Create one in the Authors tab.": "Todavía no hay autores. Cree uno en la pestaña Autores.",
  "Manager mode": "Modo del manager",
  "Select an author...": "Seleccione un autor...",
  "one URL per line, or comma separated": "una URL por línea, o separadas por comas",
  "optional angle": "ángulo opcional",
  Author: "Autor",
  Brief: "Consigna",
  Links: "Enlaces",
  Focus: "Enfoque",
  "Pick at least one author for the batch.": "Elija al menos un autor para el lote.",
  "Pick an author for the article.": "Elija un autor para el artículo.",
  "Give a brief or at least one link.": "Indique una consigna o al menos un enlace.",
  "Starting run...": "Iniciando ejecución...",
  "Run {id} {status}...": "Ejecución {id} {status}...",
  "Run {status}.": "Ejecución {status}.",
  "Run {id} is still running; check history.": "La ejecución {id} sigue en curso; consulte el historial.",
  "Could not start run: {msg}": "No se pudo iniciar la ejecución: {msg}",
  "Loading runs...": "Cargando ejecuciones...",
  "No runs yet.": "Todavía no hay ejecuciones.",
  "Could not load runs: {msg}": "No se pudo cargar las ejecuciones: {msg}",
  Run: "Ejecución",
  Mode: "Modo",
  Requested: "Solicitados",
  Created: "Creado",
  Finished: "Finalizado",
  "Loading run...": "Cargando ejecución...",
  "Could not load run: {msg}": "No se pudo cargar la ejecución: {msg}",
  "No assignments produced.": "No se generaron asignaciones.",
  "Run assignment outcomes": "Resultados de asignaciones de la ejecución",
  Persona: "Persona",
  Section: "Sección",
  Result: "Resultado",
  Image: "Imagen",
  "Hero image for {id}'s article": "Imagen principal del artículo de {id}",

  // --- Editorial panel --------------------------------------------------
  ["Voice is the house tone the writers adopt. Exemplars are short sample passages they imitate. Rules are the " +
    "explicit dos and don'ts. Publishing mints a new immutable version and activates it; the active lexicon and " +
    "sourcing ride along so editing voice never wipes them."]:
    "La voz es el tono de la casa que adoptan los escritores. Los ejemplos son pasajes breves de muestra que imitan. " +
    "Las reglas son lo que se debe y no se debe hacer de forma explícita. Publicar crea una nueva versión inmutable y " +
    "la activa; el léxico y los criterios activos viajan con ella para que editar la voz nunca los borre.",
  ["Banned terms force a revise regardless of the LLM's verdict (they are checked in code, not judged by the model). " +
    "Preferred swaps map a term to the wording the house prefers."]:
    "Los términos prohibidos fuerzan una revisión sin importar el veredicto del LLM (se comprueban en código, no los " +
    "juzga el modelo). Los reemplazos preferidos asignan a un término la redacción que prefiere la casa.",
  ["Minimum sources is the cross-source corroboration floor: how many independent sources must carry a claim " +
    "before it can run. Leave it blank to turn the gate off (blank is not zero)."]:
    "El mínimo de fuentes es el piso de corroboración entre fuentes: cuántas fuentes independientes deben sostener " +
    "una afirmación antes de que pueda publicarse. Déjelo en blanco para desactivar el control (en blanco no es cero).",
  ["The newsroom's place and languages, used to scope the news feeds. gl, hl, and ceid are derived from these " +
    "fields and are read-only."]:
    "El lugar y los idiomas de la redacción, usados para acotar los feeds de noticias. gl, hl y ceid se derivan de " +
    "estos campos y son de solo lectura.",
  ["Every style edit publishes a new immutable version and activates it. Promoting an older version reactivates " +
    "it, which is how you roll back."]:
    "Cada edición de estilo publica una nueva versión inmutable y la activa. Promover una versión anterior la " +
    "reactiva, que es como se revierte.",
  Voice: "Voz",
  "Exemplars (JSON array)": "Ejemplos (arreglo JSON)",
  "Rules (JSON array)": "Reglas (arreglo JSON)",
  "Structure (JSON object)": "Estructura (objeto JSON)",
  "Published by (optional)": "Publicado por (opcional)",
  "Publish new version": "Publicar nueva versión",
  "Active version {v}.": "Versión activa {v}.",
  "No active style yet. Publish one below to get started.": "Aún no hay un estilo activo. Publique uno abajo para empezar.",
  "Could not load style: {msg}. Reload before publishing.":
    "No se pudo cargar el estilo: {msg}. Vuelva a cargar antes de publicar.",
  Exemplars: "Ejemplos",
  Rules: "Reglas",
  Structure: "Estructura",
  "Publishing...": "Publicando...",
  "Published a new version.": "Se publicó una nueva versión.",
  "Could not publish ({code}): {msg}": "No se pudo publicar ({code}): {msg}",
  "{label} must be valid JSON.": "{label} debe ser JSON válido.",
  "{label} must be a JSON object.": "{label} debe ser un objeto JSON.",
  Lexicon: "Léxico",
  "Banned terms (one per line)": "Términos prohibidos (uno por línea)",
  "one term per line": "un término por línea",
  "Preferred swaps (JSON object)": "Reemplazos preferidos (objeto JSON)",
  "Preferred swaps": "Reemplazos preferidos",
  "Save lexicon": "Guardar léxico",
  "Publish a style first to edit the lexicon.": "Publique un estilo primero para editar el léxico.",
  "Could not load lexicon: {msg}": "No se pudo cargar el léxico: {msg}",
  "Lexicon saved.": "Léxico guardado.",
  "Could not save lexicon ({code}): {msg}": "No se pudo guardar el léxico ({code}): {msg}",
  Sourcing: "Criterios de fuentes",
  "Minimum sources (blank = gate off)": "Mínimo de fuentes (vacío = desactivado)",
  off: "desactivado",
  "Require attribution": "Exigir atribución",
  "No fabricated quotes": "Sin citas fabricadas",
  "Save sourcing": "Guardar criterios",
  "Publish a style first to edit sourcing.": "Publique un estilo primero para editar los criterios.",
  "Could not load sourcing: {msg}": "No se pudo cargar los criterios: {msg}",
  "Minimum sources must be a whole number, or blank to turn the gate off.":
    "El mínimo de fuentes debe ser un número entero, o vacío para desactivar el control.",
  "Sourcing saved.": "Criterios guardados.",
  "Could not save sourcing ({code}): {msg}": "No se pudo guardar los criterios ({code}): {msg}",
  Location: "Ubicación",
  Region: "Región",
  "UI language": "Idioma de la interfaz",
  "GDELT country": "País GDELT",
  City: "Ciudad",
  "Lat/long": "Lat/long",
  "Derived (read-only)": "Derivados (solo lectura)",
  "Save location": "Guardar ubicación",
  "Could not load location: {msg}": "No se pudo cargar la ubicación: {msg}",
  "No changes to save.": "No hay cambios para guardar.",
  "Location saved.": "Ubicación guardada.",
  "Could not save location ({code}): {msg}": "No se pudo guardar la ubicación ({code}): {msg}",
  Versions: "Versiones",
  "Loading versions...": "Cargando versiones...",
  "No versions yet.": "Todavía no hay versiones.",
  "Promoting...": "Promoviendo...",
  "Could not promote ({code}): {msg}": "No se pudo promover ({code}): {msg}",
  "Could not load versions: {msg}": "No se pudo cargar las versiones: {msg}",

  // --- Prompts panel ----------------------------------------------------
  ["These are the stage instructions the brain runs for this step. Editing publishes a new immutable version and " +
    "makes it active right away. {{TOKEN}} placeholders (double braces) are substituted at runtime: leave them in " +
    "place, removing one breaks the step."]:
    "Estas son las instrucciones de etapa que el brain ejecuta para este paso. Editar publica una nueva versión " +
    "inmutable y la activa de inmediato. Los marcadores {{TOKEN}} (llaves dobles) se sustituyen en tiempo de " +
    "ejecución: déjelos en su lugar, quitar uno rompe el paso.",
  ["Every edit publishes a new version and activates it. Promoting an older version reactivates it, which is how " +
    "you roll a change back."]:
    "Cada edición publica una nueva versión y la activa. Promover una versión anterior la reactiva, que es como se " +
    "revierte un cambio.",
  "Prompt templates": "Plantillas de prompts",
  "Prompt template": "Plantilla de prompt",
  Template: "Plantilla",
  "No prompt templates.": "No hay plantillas de prompts.",
  Editor: "Editor",
  Body: "Cuerpo",
  "Active version {v} for {key}.": "Versión activa {v} para {key}.",
  "Could not load template: {msg}": "No se pudo cargar la plantilla: {msg}",
  "No template selected.": "Ninguna plantilla seleccionada.",
  "Body cannot be empty.": "El cuerpo no puede estar vacío.",
  "Could not load templates: {msg}": "No se pudo cargar las plantillas: {msg}",
};

export function currentLocale() {
  try {
    const v = globalThis.localStorage && localStorage.getItem(STORAGE_KEY);
    if (v === "en" || v === "es") return v;
  } catch {
    /* localStorage can be unavailable in hardened browsers. */
  }
  return DEFAULT;
}

export function setLocale(v) {
  const loc = v === "en" || v === "es" ? v : DEFAULT;
  try {
    localStorage.setItem(STORAGE_KEY, loc);
  } catch {
    /* localStorage can be unavailable in hardened browsers. */
  }
  return loc;
}

// Resolve a key for the active locale, then substitute {name} placeholders from
// params. EN is identity (the key passes straight through). The locale is read
// live so a re-mount after a switch renders the new language without reload.
export function t(key, params) {
  const loc = currentLocale();
  let s = loc === "es" && key in ES ? ES[key] : key;
  if (params) s = s.replace(/\{(\w+)\}/g, (_, k) => (params[k] != null ? String(params[k]) : ""));
  return s;
}

export const LANGS = ["en", "es"];
export const LANG_LABEL = { en: "EN", es: "ES" };
