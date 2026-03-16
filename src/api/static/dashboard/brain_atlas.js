(function () {
  function readPageData() {
    const node = document.getElementById("atlas-page-data");
    if (!node || !node.textContent) {
      return null;
    }
    try {
      return JSON.parse(node.textContent);
    } catch {
      return null;
    }
  }

  function polarPosition(index, total, radiusX, radiusY, centerX, centerY) {
    const angle = (Math.PI * 2 * index) / Math.max(total, 1) - Math.PI / 2;
    return {
      x: centerX + Math.cos(angle) * radiusX,
      y: centerY + Math.sin(angle) * radiusY,
    };
  }

  function renderAtlas() {
    const data = readPageData();
    const container = document.querySelector("[data-atlas-map]");
    const detail = document.querySelector("[data-atlas-detail]");
    if (!data || !container || !detail) {
      return;
    }

    const facets = Array.isArray(data.facets) ? data.facets : [];
    const links = Array.isArray(data.links) ? data.links : [];
    const currentHeadspace = Array.isArray(data.current_headspace) ? data.current_headspace : [];
    const currentHeadspaceById = new Map(currentHeadspace.map((item) => [item.facet_id, item]));
    const width = container.clientWidth || 900;
    const height = container.clientHeight || 640;
    const centerX = width / 2;
    const centerY = height / 2;

    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.setAttribute("preserveAspectRatio", "none");
    container.appendChild(svg);

    const nodesById = new Map();
    const groupOrder = ["projects", "stories", "ideas", "thoughts", "interests", "media", "people", "systems"];
    const grouped = new Map();
    groupOrder.forEach((name) => grouped.set(name, facets.filter((item) => item.facet_type === name).slice(0, 8)));

    const rings = [
      { radiusX: width * 0.18, radiusY: height * 0.16 },
      { radiusX: width * 0.32, radiusY: height * 0.28 },
      { radiusX: width * 0.44, radiusY: height * 0.38 },
    ];

    const positioned = [];
    groupOrder.forEach((groupName, groupIndex) => {
      const groupItems = (grouped.get(groupName) || []).sort((left, right) => {
        const leftPath = Number((currentHeadspaceById.get(left.id) || {}).path_score || 0);
        const rightPath = Number((currentHeadspaceById.get(right.id) || {}).path_score || 0);
        return rightPath - leftPath;
      });
      const ring = rings[groupIndex % rings.length];
      groupItems.forEach((facet, itemIndex) => {
        const headspaceNode = currentHeadspaceById.get(facet.id) || null;
        const point = polarPosition(
          itemIndex + groupIndex * 0.7,
          Math.max(groupItems.length, 4),
          ring.radiusX,
          ring.radiusY,
          centerX,
          centerY,
        );
        const size = 72 + Math.round((facet.attention_score || 0) * 38) + Math.round(Number((headspaceNode || {}).path_score || 0) * 22);
        positioned.push({ facet, point, size });
        nodesById.set(facet.id, { x: point.x, y: point.y });
      });
    });

    links.forEach((link) => {
      const source = nodesById.get(link.source_id);
      const target = nodesById.get(link.target_id);
      if (!source || !target) {
        return;
      }
      const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      line.setAttribute("x1", String(source.x));
      line.setAttribute("y1", String(source.y));
      line.setAttribute("x2", String(target.x));
      line.setAttribute("y2", String(target.y));
      line.setAttribute("stroke", "rgba(251, 191, 36, 0.20)");
      line.setAttribute("stroke-width", String(Math.max(1, (link.weight || 0) * 2.6)));
      line.setAttribute("stroke-linecap", "round");
      svg.appendChild(line);
    });

    function renderDetail(facet) {
      const related = facets.filter((item) => (facet.related_ids || []).includes(item.id));
      const evidence = Array.isArray(facet.evidence) ? facet.evidence : [];
      const openLoops = Array.isArray(facet.open_loops) ? facet.open_loops : [];
      const headspaceNode = currentHeadspaceById.get(facet.id) || null;
      detail.innerHTML = `
        <div class="atlas-panel-card">
          <div class="atlas-detail-title">
            <div>
              <div class="atlas-kicker">${facet.facet_type}</div>
              <h2>${facet.title}</h2>
            </div>
            <span class="atlas-pill atlas-pill--warm">${facet.icon || facet.facet_type}</span>
          </div>
          <p>${facet.summary || "No summary yet."}</p>
          <div class="atlas-meta">
            <span>Attention ${Number(facet.attention_score || 0).toFixed(2)}</span>
            <span>Recency ${Number(facet.recency_score || 0).toFixed(2)}</span>
            ${
              headspaceNode
                ? `<span>Path ${Number(headspaceNode.path_score || 0).toFixed(2)}</span><span>Anchors ${Number(headspaceNode.anchor_count || 0)}</span>`
                : ""
            }
            <span>${facet.happened_at_local || "unknown time"}</span>
          </div>
        </div>
        <div class="atlas-panel-card">
          <div class="atlas-section-title">Why now</div>
          <p>${headspaceNode ? headspaceNode.why_now : "This node is visible in the atlas, but it is not currently one of the strongest time-aware headspace nodes."}</p>
        </div>
        <div class="atlas-panel-card">
          <div class="atlas-section-title">Open loops</div>
          ${
            openLoops.length
              ? `<div class="atlas-tag-grid">${openLoops
                  .map((value) => `<span class="atlas-chip">${value}</span>`)
                  .join("")}</div>`
              : '<div class="atlas-empty">No explicit open loops on this node.</div>'
          }
        </div>
        <div class="atlas-panel-card">
          <div class="atlas-section-title">Evidence</div>
          <div class="atlas-list">
            ${
              evidence.length
                ? evidence
                    .map(
                      (item) => `
                        <div class="atlas-list-item">
                          <strong>${item.title || "Evidence"}</strong>
                          <div>${item.summary || ""}</div>
                          <div class="atlas-meta">
                            <span>${item.signal_kind || "unknown"}</span>
                            <span>${item.happened_at_local || "unknown time"}</span>
                          </div>
                        </div>
                      `,
                    )
                    .join("")
                : '<div class="atlas-empty">No evidence attached yet.</div>'
            }
          </div>
        </div>
        <div class="atlas-panel-card">
          <div class="atlas-section-title">Related nodes</div>
          ${
            related.length
              ? `<div class="atlas-tag-grid">${related
                  .map((item) => `<span class="atlas-chip">${item.title}</span>`)
                  .join("")}</div>`
              : '<div class="atlas-empty">No explicit related nodes on this panel yet.</div>'
          }
        </div>
      `;
    }

    let activeButton = null;
    positioned.forEach(({ facet, point, size }) => {
      const headspaceNode = currentHeadspaceById.get(facet.id) || null;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "atlas-node";
      if (headspaceNode) {
        button.classList.add("is-current");
      }
      button.style.left = `${point.x}px`;
      button.style.top = `${point.y}px`;
      button.style.width = `${size}px`;
      button.style.height = `${size}px`;
      button.style.background = `radial-gradient(circle at 35% 28%, rgba(255,255,255,0.22), transparent 20%), ${facet.color}`;
      button.innerHTML = `
        <span class="atlas-node-label">${facet.title}</span>
        <span class="atlas-node-meta">${facet.facet_type}</span>
      `;
      button.addEventListener("click", () => {
        if (activeButton) {
          activeButton.classList.remove("is-active");
        }
        activeButton = button;
        activeButton.classList.add("is-active");
        renderDetail(facet);
      });
      container.appendChild(button);
    });

    if (positioned[0]) {
      container.querySelector(".atlas-node")?.classList.add("is-active");
      renderDetail(positioned[0].facet);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", renderAtlas);
  } else {
    renderAtlas();
  }
})();
