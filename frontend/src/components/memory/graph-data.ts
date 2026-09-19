import type { CytoscapeElements, CytoscapeNodeData, GraphNode, GraphSubgraph } from "@/lib/types";

export const NODE_COLORS: Record<string, string> = {
  Rule: "#34d399",
  HumanCorrection: "#c084fc",
  Decision: "#38bdf8",
  Exception: "#fbbf24",
  Hypothesis: "#818cf8",
  AuditFinding: "#fb7185",
  Observation: "#2dd4bf",
  AgentRun: "#a3e635",
  Transaction: "#94a3b8",
  Invoice: "#cbd5e1",
  Payment: "#a8a29e",
  Vendor: "#f9a8d4",
  Customer: "#fdba74",
  Employee: "#fca5a5",
  Account: "#67e8f9",
  Period: "#d4d4d8",
  PurchaseOrder: "#c4b5fd",
};

export const DEFAULT_LABELS = ["Rule", "HumanCorrection", "Decision", "Exception"];
export const EMPTY_GRAPH: CytoscapeElements = { nodes: [], edges: [] };

export function nodeName(node: GraphNode): string {
  for (const key of ["name", "title", "description", "explanation", "pattern_type"]) {
    const value = node.props[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return node.id;
}

export function graphCaption(data: CytoscapeNodeData): string {
  for (const key of ["name", "title", "description"]) {
    const value = data[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return data.id;
}

export function provenanceElements(chain: GraphSubgraph): CytoscapeElements {
  const ids = new Set(chain.nodes.map((node) => node.id));
  return {
    nodes: chain.nodes.map((node) => ({ data: { ...node.props, id: node.id, label: node.label, name: nodeName(node) } })),
    edges: chain.edges.filter((edge) => ids.has(edge.src) && ids.has(edge.dst)).map((edge) => ({
      data: { id: edge.id, source: edge.src, target: edge.dst, rel: edge.rel },
    })),
  };
}
