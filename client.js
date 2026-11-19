// picture_pokemon, Spectra image archetype. A random Pokémon TCG card,
// shown whole (object-fit: contain, never cropped: a card's frame is part
// of the picture) on a --surface-sunken mat, the way a card sits in a
// binder sleeve.
//
//   xs, sm  the card alone, centred.
//   md, lg  the card plus a details panel: where it was picked from, the
//           name, stage and HP, type chips, set and number, rarity, and
//           the illustrator. A landscape cell puts the panel beside the
//           card; a portrait cell puts a slimmer one under it.
//
// The show_details option drops the panel at md/lg, leaving the sm look.
// Type chips carry the TCG's own energy colours (the "colour is part of
// the data" carve-out in docs/widgets.md); everything else paints from
// tokens.

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// The energy colours printed on the cards.
const TYPE_COLOURS = {
  Grass: "#439837",
  Fire: "#E4613E",
  Water: "#3099E1",
  Lightning: "#DFBC28",
  Psychic: "#9B5BA5",
  Fighting: "#C7752F",
  Darkness: "#2F3A40",
  Metal: "#8E9AA3",
  Dragon: "#B39A2E",
  Fairy: "#E18CB8",
  Colorless: "#C9C3B6",
};

const LAYOUT = `
  .pk-body {
    display: flex;
    flex-direction: row;
    align-items: stretch;
    justify-content: center;
    gap: var(--space-4);
    height: 100%;
    box-sizing: border-box;
    padding: var(--space-3);
    background: var(--surface-sunken);
    min-width: 0;
    min-height: 0;
  }
  .pk-card {
    flex: 0 1 auto;
    height: 100%;
    aspect-ratio: 600 / 825;
    max-width: 100%;
    min-width: 0;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .pk-card img {
    display: block;
    width: 100%;
    height: 100%;
    object-fit: contain;
  }

  .pk-info {
    flex: 1 1 0;
    min-width: 0;
    display: flex;
    flex-direction: column;
    justify-content: center;
    gap: var(--space-2);
    padding: var(--space-2) var(--space-3);
    background: var(--surface);
    color: var(--text-primary);
    font-size: clamp(12px, 4.2cqmin, 26px);
    overflow: hidden;
  }
  .pk-from {
    font-size: var(--fs-label);
    font-weight: var(--fw-bold);
    letter-spacing: var(--ls-label);
    text-transform: var(--label-transform, uppercase);
    color: var(--text-secondary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .pk-from .ph-bold { color: var(--accent-2); margin-right: 0.3em; }
  .pk-name {
    font-size: var(--fs-value);
    font-weight: var(--fw-black);
    line-height: var(--lh-tight);
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
    overflow-wrap: anywhere;
  }
  .pk-stage {
    font-size: var(--fs-body);
    font-weight: var(--fw-semi);
    color: var(--text-secondary);
    font-variant-numeric: tabular-nums;
  }
  .pk-chips { display: flex; flex-wrap: wrap; gap: var(--space-1); }
  .pk-chip {
    display: inline-flex;
    align-items: center;
    gap: 0.4em;
    padding: 0.2em 0.6em;
    border-radius: var(--pill-radius, 0);
    background: var(--surface-sunken);
    color: var(--text-primary);
    font-size: var(--fs-caption);
    font-weight: var(--fw-bold);
    letter-spacing: var(--ls-label);
    text-transform: var(--label-transform, uppercase);
    white-space: nowrap;
  }
  .pk-chip .swatch {
    width: 0.9em;
    height: 0.9em;
    border-radius: 50%;
    flex: 0 0 auto;
  }
  .pk-chip.is-rarity { background: var(--accent-2-soft); }
  .pk-chip.is-rarity .ph-bold { color: var(--accent-2); }
  .pk-set {
    display: flex;
    flex-direction: column;
    gap: 0.1em;
    min-width: 0;
  }
  .pk-set .set {
    font-size: var(--fs-body);
    font-weight: var(--fw-bold);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .pk-set .num {
    font-size: var(--fs-label);
    font-weight: var(--fw-semi);
    color: var(--text-secondary);
    font-variant-numeric: tabular-nums;
  }
  .pk-illus {
    font-size: var(--fs-caption);
    font-weight: var(--fw-semi);
    color: var(--text-muted);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  /* Portrait cell: the card on top, a slim band under it. */
  @container (orientation: portrait) {
    .pk-body { flex-direction: column; align-items: center; gap: var(--space-3); }
    .pk-card { flex: 1 1 auto; height: auto; width: 100%; min-height: 0; }
    .pk-info {
      flex: 0 0 auto;
      align-self: stretch;
      gap: var(--space-1);
      font-size: clamp(12px, 3.6cqmin, 22px);
    }
    .pk-name { -webkit-line-clamp: 1; font-size: var(--fs-lead); }
    .pk-illus, .pk-from { display: none; }
  }

  /* Empty state: one big bold icon on a soft accent block. */
  .pk-empty {
    flex: 1 1 auto;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: var(--space-3);
    height: 100%;
    box-sizing: border-box;
    background: var(--accent-2-soft);
    color: var(--text-secondary);
    font-size: var(--fs-lead);
    font-weight: var(--fw-bold);
    text-align: center;
    padding: var(--pad);
  }
  .pk-empty .ph-bold { font-size: clamp(48px, 30cqmin, 144px); color: var(--accent-2); }
`;

function details(data, t) {
  const from = data.pool
    ? `${t("random_from", "Random from")} ${data.pool}`
    : t("random_card", "Random card");

  const stage = [];
  if (data.category === "Pokemon") {
    // TCGdex spells it "Stage2"; the cards say "Stage 2".
    if (data.stage) stage.push(escapeHtml(String(data.stage).replace(/^Stage(\d)/, "Stage $1")));
    if (data.hp) stage.push(`${escapeHtml(t("hp", "HP"))} ${escapeHtml(data.hp)}`);
  } else if (data.category) {
    stage.push(escapeHtml(data.category));
  }

  const chips = (Array.isArray(data.types) ? data.types : []).map((type) => {
    const colour = TYPE_COLOURS[type] || "var(--text-muted)";
    return `<span class="pk-chip"><span class="swatch" style="background:${colour}"></span>${escapeHtml(type)}</span>`;
  });
  if (data.rarity) {
    chips.push(`<span class="pk-chip is-rarity"><i class="ph-bold ph-star" aria-hidden="true"></i>${escapeHtml(data.rarity)}</span>`);
  }

  const set = data.set_name || data.number
    ? `<div class="pk-set">
         ${data.set_name ? `<span class="set">${escapeHtml(data.set_name)}</span>` : ""}
         ${data.number ? `<span class="num">${escapeHtml(t("number", "No."))} ${escapeHtml(data.number)}</span>` : ""}
       </div>`
    : "";

  return `
    <div class="pk-info">
      <div class="pk-from"><i class="ph-bold ph-shuffle" aria-hidden="true"></i>${escapeHtml(from)}</div>
      <div class="pk-name">${escapeHtml(data.name)}</div>
      ${stage.length ? `<div class="pk-stage">${stage.join(" · ")}</div>` : ""}
      ${chips.length ? `<div class="pk-chips">${chips.join("")}</div>` : ""}
      ${set}
      ${data.illustrator ? `<div class="pk-illus">${escapeHtml(t("illustrated_by", "Illus."))} ${escapeHtml(data.illustrator)}</div>` : ""}
    </div>`;
}

export default function render(shadow, ctx) {
  const t = ctx?.t || ((key, fallback) => fallback ?? key);
  const data = ctx?.data ?? {};
  const opts = ctx?.cell?.options || {};
  const size = ctx?.cell?.size || "md";
  const css = `<link rel="stylesheet" href="/static/style/spectra-widgets.css"><style>${LAYOUT}</style>`;

  if (data.error) {
    shadow.innerHTML = `
      ${css}
      <div class="w" data-widget="picture_pokemon">
        <div class="w-title"><i class="ph-bold ph-warning-circle" style="color:var(--accent-1)"></i><h3>${escapeHtml(t("pokemon_card", "Pokémon card"))}</h3></div>
        <div class="w-body"><p class="u-muted">${escapeHtml(data.error)}</p></div>
      </div>`;
    return;
  }

  if (!data.url) {
    shadow.innerHTML = `
      ${css}
      <div class="w is-bleed" data-widget="picture_pokemon">
        <div class="pk-empty">
          <i class="ph-bold ph-cards" aria-hidden="true"></i>
          <span>${escapeHtml(t("no_card", "No card to show."))}</span>
        </div>
      </div>`;
    return;
  }

  const showDetails = (size === "md" || size === "lg") && opts.show_details !== false
    && Boolean(data.name);

  shadow.innerHTML = `
    ${css}
    <div class="w is-bleed size-${escapeHtml(size)}" data-widget="picture_pokemon">
      <div class="pk-body">
        <div class="pk-card"><img src="${escapeHtml(data.url)}" alt="${escapeHtml(data.name || t("pokemon_card", "Pokémon card"))}"></div>
        ${showDetails ? details(data, t) : ""}
      </div>
    </div>`;
}
