"use client";

import { useEffect, useRef, useState } from "react";
import cytoscape, { type Core, type EventObjectNode, type StylesheetStyle } from "cytoscape";
import fcose from "cytoscape-fcose";
import { Expand, Minus, Plus, RotateCcw } from "lucide-react";
import { graphCaption, NODE_COLORS } from "@/components/memory/graph-data";
import { Button } from "@/components/ui/button";
import type { CytoscapeElements } from "@/lib/types";

cytoscape.use(fcose);

const STYLES: StylesheetStyle[] = [
  { selector: "node", style: {
    width: 27, height: 27, "background-color": "data(color)", "border-width": 2,
    "border-color": "#101012", label: "data(caption)", color: "#b8b8c4", "font-size": 10,
    "font-family": "ui-monospace, monospace", "text-valign": "bottom", "text-margin-y": 8,
    "text-wrap": "ellipsis", "text-max-width": "112px", "text-outline-color": "#101012", "text-outline-width": 2,
  } },
  { selector: 'node[label = "Rule"]', style: { shape: "diamond", width: 38, height: 38 } },
  { selector: 'node[label = "HumanCorrection"]', style: { shape: "round-rectangle", width: 34, height: 34 } },
  { selector: "edge", style: {
    width: 1, "line-color": "#33333f", "target-arrow-color": "#484858", "target-arrow-shape": "triangle",
    "arrow-scale": 0.6, "curve-style": "bezier", opacity: 0.8, "font-size": 8, color: "#b8b8c4",
    "text-background-color": "#101012", "text-background-opacity": 0.95, "text-background-padding": "3px",
    "text-rotation": "autorotate",
  } },
  { selector: ".labels-hidden", style: { label: "" } },
  { selector: ".is-selected", style: { "border-color": "#ffffff", "border-width": 3, color: "#ffffff", label: "data(caption)" } },
  { selector: ".is-dim", style: { opacity: 0.14 } },
  { selector: "node.trace-lit", style: { "border-color": "#34d399", "border-width": 3, opacity: 1 } },
  { selector: "edge.trace-lit", style: { "line-color": "#34d399", "target-arrow-color": "#34d399", width: 2, opacity: 1, label: "data(rel)" } },
  { selector: "node.trace-active", style: { "border-color": "#ffffff", "border-width": 5, color: "#ffffff", label: "data(caption)" } },
  { selector: "edge:active", style: { label: "data(rel)", "line-color": "#a1a1aa" } },
];

const LAYOUT = { name: "fcose", quality: "default", randomize: true, animate: false, fit: true, padding: 45, nodeSeparation: 100, idealEdgeLength: 100, numIter: 1500, packComponents: false };

export default function GraphCanvas({ elements, selectedId, onSelect, showLabels, highlightedIds, tracing }: {
  elements: CytoscapeElements; selectedId: string | null; onSelect: (id: string) => void;
  showLabels: boolean; highlightedIds: string[]; tracing: boolean;
}) {
  const container = useRef<HTMLDivElement>(null);
  const graph = useRef<Core | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!container.current) return;
    const instance = cytoscape({
      container: container.current,
      elements: [],
      style: STYLES,
      minZoom: 0.08,
      maxZoom: 3,
      wheelSensitivity: 0.2,
      boxSelectionEnabled: false,
      autounselectify: true,
      layout: { name: "preset" },
    });
    graph.current = instance;
    const select = (event: EventObjectNode) => onSelect(event.target.id());
    instance.on("tap", "node", select);
    const observer = new ResizeObserver(() => instance.resize());
    observer.observe(container.current);
    return () => { observer.disconnect(); instance.destroy(); graph.current = null; };
  }, [onSelect]);

  useEffect(() => {
    const instance = graph.current;
    if (!instance) return;
    let cancelled = false;
    const ids = new Set(elements.nodes.map((node) => node.data.id));
    instance.batch(() => {
      instance.elements().remove();
      instance.add([
        ...elements.nodes.map(({ data }) => ({ data: { ...data, color: NODE_COLORS[data.label] ?? "#a1a1aa", caption: graphCaption(data) } })),
        ...elements.edges.filter(({ data }) => ids.has(data.source) && ids.has(data.target)),
      ]);
    });
    const task = window.setTimeout(() => {
      try {
        if (!cancelled) { instance.layout(elements.nodes.length > 1 ? LAYOUT : { name: "grid", padding: 60 }).run(); setError(null); }
      } catch {
        if (!cancelled) { instance.layout({ name: "circle", padding: 45 }).run(); setError("Force layout unavailable. Showing a circular layout."); }
      }
    }, 0);
    return () => { cancelled = true; window.clearTimeout(task); };
  }, [elements, onSelect]);

  useEffect(() => {
    const instance = graph.current;
    if (!instance) return;
    instance.batch(() => {
      instance.elements().removeClass("is-selected is-dim trace-lit trace-active");
      instance.nodes().toggleClass("labels-hidden", !showLabels);
      if (selectedId) instance.getElementById(selectedId).addClass("is-selected");
      if (tracing) {
        const visited = new Set(highlightedIds);
        instance.elements().addClass("is-dim");
        highlightedIds.forEach((id) => instance.getElementById(id).removeClass("is-dim").addClass("trace-lit"));
        instance.edges().forEach((edge) => {
          if (visited.has(edge.source().id()) && visited.has(edge.target().id())) edge.removeClass("is-dim").addClass("trace-lit");
        });
        const active = highlightedIds.at(-1);
        if (active) instance.getElementById(active).addClass("trace-active");
      }
    });
  }, [elements, selectedId, showLabels, highlightedIds, tracing, onSelect]);

  function zoom(factor: number) {
    const instance = graph.current;
    if (!instance) return;
    instance.zoom({ level: instance.zoom() * factor, renderedPosition: { x: instance.width() / 2, y: instance.height() / 2 } });
  }

  return (
    <div className="relative">
      <div ref={container} role="img" aria-label={`Financial memory graph with ${elements.nodes.length} nodes and ${elements.edges.length} relationships. Use the node directory below to explore with a keyboard.`}
        className="h-[480px] w-full bg-grid sm:h-[560px]" />
      <div className="absolute right-3 top-3 flex flex-col gap-1 rounded-lg border border-border-strong bg-surface/95 p-1">
        <Button variant="ghost" size="icon-sm" aria-label="Zoom in" title="Zoom in" onClick={() => zoom(1.3)}><Plus aria-hidden /></Button>
        <Button variant="ghost" size="icon-sm" aria-label="Zoom out" title="Zoom out" onClick={() => zoom(1 / 1.3)}><Minus aria-hidden /></Button>
        <Button variant="ghost" size="icon-sm" aria-label="Fit graph to view" title="Fit graph" onClick={() => graph.current?.fit(undefined, 45)}><Expand aria-hidden /></Button>
        <Button variant="ghost" size="icon-sm" aria-label="Rearrange graph" title="Rearrange graph" onClick={() => graph.current?.layout({ name: "circle", padding: 45 }).run()}><RotateCcw aria-hidden /></Button>
      </div>
      {error && <p role="status" className="absolute bottom-3 left-3 right-3 rounded border border-amber-400/20 bg-surface p-2 text-xs text-amber-200">{error}</p>}
    </div>
  );
}
