import React, { useMemo, useState } from 'react';
import {
  NetworkTopology,
  NetworkNode,
  NetworkEdge,
  PodState,
  SwarmState,
  DemandMapResponse,
  RouteSwitchResponse,
} from '../types/api';
import { Layers, Activity, Zap, AlertTriangle, Battery, Navigation, Radio, X } from 'lucide-react';

interface CityMapProps {
  topology: NetworkTopology | null;
  pods: PodState[];
  swarms: SwarmState[];
  edgeCongestions: Record<string, any>;
  demandMap: DemandMapResponse | null;
  routeSwitch: RouteSwitchResponse | null;
  viewMode: 'TRAFFIC' | 'DEMAND' | 'SWARMS';
  onViewModeChange: (mode: 'TRAFFIC' | 'DEMAND' | 'SWARMS') => void;
  selectedNodeId: string | null;
  onSelectNode: (nodeId: string | null) => void;
  selectedPodId?: string | null;
  onSelectPod?: (podId: string | null) => void;
  onInjectTraffic?: (edgeId: string) => void;
}

export const CityMap: React.FC<CityMapProps> = ({
  topology,
  pods,
  swarms,
  edgeCongestions,
  demandMap,
  routeSwitch,
  viewMode,
  onViewModeChange,
  selectedNodeId,
  onSelectNode,
  selectedPodId,
  onSelectPod,
  onInjectTraffic,
}) => {
  const [hoveredNode, setHoveredNode] = useState<NetworkNode | null>(null);
  const [hoveredPod, setHoveredPod] = useState<PodState | null>(null);

  // SVG dimensions and projection
  const width = 850;
  const height = 550;
  const padding = 45;

  const nodePositions = useMemo(() => {
    if (!topology || topology.nodes.length === 0) {
      return new Map<string, { x: number; y: number; node: NetworkNode }>();
    }

    const { min_lat, max_lat, min_lon, max_lon } = topology.bounds;
    const latSpan = max_lat - min_lat || 0.01;
    const lonSpan = max_lon - min_lon || 0.01;

    const map = new Map<string, { x: number; y: number; node: NetworkNode }>();
    topology.nodes.forEach((n) => {
      // WGS84: longitude -> X, latitude -> Y (inverted Y for SVG canvas)
      const x = padding + ((n.longitude - min_lon) / lonSpan) * (width - 2 * padding);
      const y = height - padding - ((n.latitude - min_lat) / latSpan) * (height - 2 * padding);
      map.set(n.node_id, { x, y, node: n });
    });
    return map;
  }, [topology]);

  const activeSwarmMap = useMemo(() => {
    const map = new Map<string, SwarmState>();
    swarms.forEach((s) => map.set(s.swarm_id, s));
    return map;
  }, [swarms]);

  // Group docked (idle/charging) pods by station node
  const dockedPodsByNode = useMemo(() => {
    const map = new Map<string, PodState[]>();
    pods.forEach((p) => {
      if (p.status === 'idle' || p.status === 'charging') {
        const list = map.get(p.current_node_id) || [];
        list.push(p);
        map.set(p.current_node_id, list);
      }
    });
    return map;
  }, [pods]);

  // Selected pod object
  const activeSelectedPod = useMemo(() => {
    if (!selectedPodId) return null;
    return pods.find((p) => p.pod_id === selectedPodId) || null;
  }, [selectedPodId, pods]);

  if (!topology) {
    return (
      <div className="w-full h-full flex items-center justify-center bg-[#07090e] border border-slate-800/80 rounded-lg">
        <div className="flex flex-col items-center gap-3 text-slate-500">
          <Activity className="w-8 h-8 animate-spin text-cyan-400" />
          <span className="text-xs uppercase tracking-wider font-mono">Synthesizing Network Topology...</span>
        </div>
      </div>
    );
  }

  return (
    <div className="relative w-full h-full flex flex-col bg-[#080c13] border border-slate-800 rounded-lg overflow-hidden shadow-2xl">
      {/* Top Map Bar: Mode Toggles and Map Controls */}
      <div className="absolute top-3 left-4 right-4 z-20 flex items-center justify-between pointer-events-none">
        <div className="flex items-center gap-2 pointer-events-auto bg-[#0c1017]/95 backdrop-blur-md px-3 py-1.5 rounded-md border border-slate-800/90 shadow-lg">
          <Layers className="w-4 h-4 text-cyan-400 mr-1" />
          {(['TRAFFIC', 'DEMAND', 'SWARMS'] as const).map((mode) => (
            <button
              key={mode}
              onClick={() => onViewModeChange(mode)}
              className={`px-3 py-1 text-xs font-mono font-medium rounded transition-all ${
                viewMode === mode
                  ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/40 shadow-sm'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/40'
              }`}
            >
              {mode}
            </button>
          ))}
        </div>

        {/* Legend */}
        <div className="flex items-center gap-3.5 bg-[#0c1017]/95 backdrop-blur-md px-3 py-1.5 rounded-md border border-slate-800/90 text-xs font-mono text-slate-400 pointer-events-auto">
          <div className="flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 rounded-full bg-cyan-400 border border-cyan-200"></span>
            <span>Station</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 bg-amber-400 rotate-45 border border-amber-200"></span>
            <span>Terminal</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-slate-500"></span>
            <span>Intersection</span>
          </div>
          <div className="flex items-center gap-1.5 border-l border-slate-800 pl-2.5">
            <span className="w-2.5 h-2.5 rounded-full bg-emerald-400 border border-emerald-200"></span>
            <span>Swarm Platoon</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 rounded-sm bg-purple-500"></span>
            <span>Reposition</span>
          </div>
        </div>
      </div>

      {/* SVG Canvas Map */}
      <div className="flex-1 w-full h-full flex items-center justify-center p-2">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          className="w-full h-full max-h-[600px] select-none"
          style={{ background: 'radial-gradient(ellipse at center, #0e1624 0%, #080c13 75%)' }}
        >
          <defs>
            {/* Edge arrowhead markers */}
            <marker id="arrow" viewBox="0 0 10 10" refX="14" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
              <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="rgba(148, 163, 184, 0.45)" />
            </marker>
            <marker id="arrow-congested" viewBox="0 0 10 10" refX="14" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
              <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="#ef4444" />
            </marker>
            <marker id="arrow-cyan" viewBox="0 0 10 10" refX="14" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
              <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="#00f0ff" />
            </marker>
            <marker id="arrow-flow" viewBox="0 0 10 10" refX="12" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="rgba(0, 240, 255, 0.7)" />
            </marker>

            {/* Radial gradients for demand hotspots */}
            <radialGradient id="deficit-glow" cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor="#ff3366" stopOpacity="0.55" />
              <stop offset="70%" stopColor="#ff3366" stopOpacity="0.18" />
              <stop offset="100%" stopColor="#ff3366" stopOpacity="0" />
            </radialGradient>
            <radialGradient id="surplus-glow" cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor="#10b981" stopOpacity="0.5" />
              <stop offset="70%" stopColor="#10b981" stopOpacity="0.15" />
              <stop offset="100%" stopColor="#10b981" stopOpacity="0" />
            </radialGradient>

            {/* Swarm corridor glow filter */}
            <filter id="corridor-glow" x="-20%" y="-20%" width="140%" height="140%">
              <feGaussianBlur stdDeviation="3.5" result="blur" />
              <feComposite in="SourceGraphic" in2="blur" operator="over" />
            </filter>
          </defs>

          {/* Grid lines */}
          <g stroke="rgba(255, 255, 255, 0.025)" strokeWidth="1">
            {Array.from({ length: 9 }).map((_, i) => (
              <line key={`gx-${i}`} x1={(i + 1) * 90} y1="0" x2={(i + 1) * 90} y2={height} />
            ))}
            {Array.from({ length: 6 }).map((_, i) => (
              <line key={`gy-${i}`} x1="0" y1={(i + 1) * 90} x2={width} y2={(i + 1) * 90} />
            ))}
          </g>

          {/* EDGES LAYER with 2.5px lateral lane separation for bidirectional roads */}
          <g className="edges-layer">
            {topology.edges.map((edge) => {
              const src = nodePositions.get(edge.source);
              const dst = nodePositions.get(edge.destination);
              if (!src || !dst) return null;

              const congestion = edgeCongestions[edge.edge_id];
              const u = congestion ? congestion.utilization : edge.utilization;

              // Lateral offset to separate forward and reverse lanes
              const dx = dst.x - src.x;
              const dy = dst.y - src.y;
              const len = Math.hypot(dx, dy) || 1;
              const nx = -dy / len;
              const ny = dx / len;
              const lateralOffset = 2.5;

              const x1 = src.x + nx * lateralOffset;
              const y1 = src.y + ny * lateralOffset;
              const x2 = dst.x + nx * lateralOffset;
              const y2 = dst.y + ny * lateralOffset;

              // Color determination
              let strokeColor = 'rgba(71, 85, 105, 0.35)';
              let strokeWidth = edge.road_type === 'trunk' ? 3.2 : edge.road_type === 'arterial' ? 2.0 : 1.2;

              if (viewMode === 'TRAFFIC') {
                if (u > 1.0) {
                  strokeColor = '#ef4444';
                  strokeWidth += 2.0;
                } else if (u > 0.75) {
                  strokeColor = '#f59e0b';
                  strokeWidth += 1.2;
                } else if (u > 0.4) {
                  strokeColor = '#06b6d4';
                }
              }

              // Route switch comparison indicators
              const isOldRoute = routeSwitch && routeSwitch.before_route.edge_ids.includes(edge.edge_id);
              const isNewRoute = routeSwitch && routeSwitch.after_route.edge_ids.includes(edge.edge_id);
              const isSelectedPodRoute = activeSelectedPod && activeSelectedPod.route_edges.includes(edge.edge_id);

              return (
                <g
                  key={edge.edge_id}
                  className="cursor-pointer"
                  onClick={() => onInjectTraffic && onInjectTraffic(edge.edge_id)}
                >
                  <line
                    x1={x1}
                    y1={y1}
                    x2={x2}
                    y2={y2}
                    stroke={strokeColor}
                    strokeWidth={strokeWidth}
                    strokeLinecap="round"
                    markerEnd={u > 1.0 ? 'url(#arrow-congested)' : 'url(#arrow)'}
                    className="transition-colors duration-300"
                  />

                  {/* Route switch: original congested route */}
                  {isOldRoute && (
                    <line
                      x1={x1}
                      y1={y1}
                      x2={x2}
                      y2={y2}
                      stroke="#f59e0b"
                      strokeWidth="3.5"
                      strokeDasharray="6 4"
                      className="animate-pulse"
                      opacity="0.85"
                    />
                  )}

                  {/* Route switch: dynamic diverted route */}
                  {isNewRoute && (
                    <line
                      x1={x1}
                      y1={y1}
                      x2={x2}
                      y2={y2}
                      stroke="#00f0ff"
                      strokeWidth="3.2"
                      strokeOpacity="0.95"
                    />
                  )}

                  {/* Selected Pod Route Path */}
                  {isSelectedPodRoute && (
                    <line
                      x1={x1}
                      y1={y1}
                      x2={x2}
                      y2={y2}
                      stroke="#a855f7"
                      strokeWidth="3.0"
                      strokeDasharray="4 3"
                      strokeOpacity="0.9"
                    />
                  )}
                </g>
              );
            })}
          </g>

          {/* SWARM CORRIDORS HIGHLIGHT (if in SWARMS mode) */}
          {viewMode === 'SWARMS' &&
            swarms.map((swarm) => {
              const orig = nodePositions.get(swarm.origin_node_id);
              const div = nodePositions.get(swarm.divergence_node_id);
              if (!orig || !div) return null;

              return (
                <g key={`corridor-${swarm.swarm_id}`}>
                  <line
                    x1={orig.x}
                    y1={orig.y}
                    x2={div.x}
                    y2={div.y}
                    stroke="#10b981"
                    strokeWidth="6"
                    strokeOpacity="0.35"
                    strokeLinecap="round"
                    filter="url(#corridor-glow)"
                  />
                  <text
                    x={(orig.x + div.x) / 2}
                    y={(orig.y + div.y) / 2 - 10}
                    fill="#10b981"
                    fontSize="10"
                    fontFamily="monospace"
                    textAnchor="middle"
                    className="font-bold pointer-events-none drop-shadow"
                  >
                    SWARM CORRIDOR: {swarm.swarm_id} ({swarm.member_pod_ids.length} PODS PLATOONING)
                  </text>
                </g>
              );
            })}

          {/* DEMAND HEATMAP & TOP OD FLOWS (if in DEMAND mode) */}
          {viewMode === 'DEMAND' && demandMap && (
            <g className="demand-layer">
              {/* Top OD Flow Arcs */}
              {demandMap.top_od_flows &&
                demandMap.top_od_flows.map((flow, idx) => {
                  const p1 = nodePositions.get(flow.origin);
                  const p2 = nodePositions.get(flow.destination);
                  if (!p1 || !p2) return null;

                  const midX = (p1.x + p2.x) / 2 + (p2.y - p1.y) * 0.18;
                  const midY = (p1.y + p2.y) / 2 - (p2.x - p1.x) * 0.18;

                  return (
                    <g key={`flow-${idx}`}>
                      <path
                        d={`M ${p1.x} ${p1.y} Q ${midX} ${midY} ${p2.x} ${p2.y}`}
                        fill="none"
                        stroke="rgba(0, 240, 255, 0.45)"
                        strokeWidth="2.5"
                        strokeDasharray="5 3"
                        markerEnd="url(#arrow-flow)"
                      />
                      <rect
                        x={midX - 22}
                        y={midY - 8}
                        width="44"
                        height="16"
                        rx="3"
                        fill="#0c1017"
                        stroke="#00f0ff"
                        strokeWidth="1"
                        opacity="0.9"
                      />
                      <text
                        x={midX}
                        y={midY + 3.5}
                        fill="#00f0ff"
                        fontSize="8.5"
                        fontFamily="monospace"
                        textAnchor="middle"
                        className="font-bold pointer-events-none"
                      >
                        {flow.trips} trips
                      </text>
                    </g>
                  );
                })}

              {/* Deficit / Surplus Hotspots */}
              {Object.entries(demandMap.nodes).map(([nid, d]) => {
                const pos = nodePositions.get(nid);
                if (!pos) return null;

                if (d.is_deficit) {
                  const radius = Math.min(50, 16 + d.deficit * 2.2);
                  return (
                    <g key={`demand-${nid}`}>
                      <circle cx={pos.x} cy={pos.y} r={radius} fill="url(#deficit-glow)" className="animate-pulse" />
                      <text
                        x={pos.x}
                        y={pos.y - radius * 0.7}
                        fill="#ff3366"
                        fontSize="9"
                        fontFamily="monospace"
                        textAnchor="middle"
                        className="font-bold drop-shadow"
                      >
                        DEFICIT -{d.deficit.toFixed(1)}
                      </text>
                    </g>
                  );
                }

                if (d.is_surplus && d.surplus > 2) {
                  const radius = Math.min(42, 12 + d.surplus * 1.4);
                  return (
                    <circle key={`surplus-${nid}`} cx={pos.x} cy={pos.y} r={radius} fill="url(#surplus-glow)" opacity="0.65" />
                  );
                }

                return null;
              })}
            </g>
          )}

          {/* PODS & SWARMS FORMATIONS LAYER */}
          <g className="pods-layer">
            {pods.map((pod) => {
              if (pod.status !== 'traveling' && pod.status !== 'assigned') return null;

              const edge = topology.edges.find((e) => e.edge_id === pod.current_edge_id);
              if (!edge) return null;

              const src = nodePositions.get(edge.source);
              const dst = nodePositions.get(edge.destination);
              if (!src || !dst) return null;

              const dx = dst.x - src.x;
              const dy = dst.y - src.y;
              const len = Math.hypot(dx, dy) || 1;
              const ux = dx / len;
              const uy = dy / len;
              const nx = -dy / len;
              const ny = dx / len;
              const lateralOffset = 2.5;

              const x1 = src.x + nx * lateralOffset;
              const y1 = src.y + ny * lateralOffset;
              const x2 = dst.x + nx * lateralOffset;
              const y2 = dst.y + ny * lateralOffset;

              const progress = Math.max(0, Math.min(1, pod.edge_progress));
              const basePx = x1 + progress * (x2 - x1);
              const basePy = y1 + progress * (y2 - y1);

              const isInSwarm = Boolean(pod.swarm_id && activeSwarmMap.has(pod.swarm_id));
              const swarm = isInSwarm ? activeSwarmMap.get(pod.swarm_id!) : null;
              const memberIdx = swarm ? swarm.member_pod_ids.indexOf(pod.pod_id) : -1;
              const isLeader = memberIdx === 0;

              // Platoon headway spacing along the edge vector: ●━━●━━●
              const platoonGap = 15;
              const px = isInSwarm && memberIdx >= 0 ? basePx - ux * memberIdx * platoonGap : basePx;
              const py = isInSwarm && memberIdx >= 0 ? basePy - uy * memberIdx * platoonGap : basePy;

              const isRepositioning = pod.trip_kind === 'repositioning';
              const isSelected = selectedPodId === pod.pod_id;
              const isHovered = hoveredPod?.pod_id === pod.pod_id;

              // Color based on pod state
              let podColor = isRepositioning ? '#a855f7' : isInSwarm ? '#10b981' : '#00f0ff';

              // Previous pod in platoon to draw coupling link: ●━━●
              let prevPx = 0;
              let prevPy = 0;
              if (isInSwarm && memberIdx > 0) {
                prevPx = basePx - ux * (memberIdx - 1) * platoonGap;
                prevPy = basePy - uy * (memberIdx - 1) * platoonGap;
              }

              return (
                <g
                  key={pod.pod_id}
                  className="cursor-pointer"
                  onClick={() => onSelectPod && onSelectPod(pod.pod_id)}
                  onMouseEnter={() => setHoveredPod(pod)}
                  onMouseLeave={() => setHoveredPod(null)}
                >
                  {/* Swarm platoon coupling bar: ●━━● */}
                  {isInSwarm && memberIdx > 0 && (
                    <line
                      x1={px}
                      y1={py}
                      x2={prevPx}
                      y2={prevPy}
                      stroke="#10b981"
                      strokeWidth="3"
                      strokeLinecap="round"
                      opacity="0.9"
                    />
                  )}

                  {/* Platoon Formation Leader Badge */}
                  {isInSwarm && isLeader && (
                    <text
                      x={px}
                      y={py - 10}
                      fill="#10b981"
                      fontSize="9"
                      fontFamily="monospace"
                      textAnchor="middle"
                      className="font-bold pointer-events-none drop-shadow"
                    >
                      ●━━● PLATOON {pod.swarm_id}
                    </text>
                  )}

                  {/* Selected ring */}
                  {isSelected && (
                    <circle cx={px} cy={py} r="9" fill="none" stroke="#00f0ff" strokeWidth="1.5" className="animate-radar" />
                  )}

                  {/* Pod Circle Marker */}
                  <circle
                    cx={px}
                    cy={py}
                    r={isInSwarm ? 4.5 : 3.5}
                    fill={podColor}
                    stroke={isSelected ? '#00f0ff' : '#07090e'}
                    strokeWidth={isSelected ? '2' : '1.5'}
                    className="shadow-lg"
                  />

                  {/* Pod ID on hover or selected */}
                  {(isSelected || isHovered) && (
                    <text
                      x={px}
                      y={py + 12}
                      fill={podColor}
                      fontSize="8"
                      fontFamily="monospace"
                      textAnchor="middle"
                      className="font-bold pointer-events-none drop-shadow"
                    >
                      {pod.pod_id}
                    </text>
                  )}
                </g>
              );
            })}
          </g>

          {/* NODES LAYER */}
          <g className="nodes-layer">
            {topology.nodes.map((node) => {
              const pos = nodePositions.get(node.node_id);
              if (!pos) return null;

              const isSelected = selectedNodeId === node.node_id;
              const isHovered = hoveredNode?.node_id === node.node_id;
              const docked = dockedPodsByNode.get(node.node_id) || [];

              if (node.node_type === 'terminal') {
                return (
                  <g
                    key={node.node_id}
                    className="cursor-pointer"
                    onClick={() => onSelectNode(node.node_id)}
                    onMouseEnter={() => setHoveredNode(node)}
                    onMouseLeave={() => setHoveredNode(null)}
                  >
                    {isSelected && <circle cx={pos.x} cy={pos.y} r="16" fill="none" stroke="#00f0ff" strokeWidth="1.5" className="animate-radar" />}
                    <rect
                      x={pos.x - 7}
                      y={pos.y - 7}
                      width="14"
                      height="14"
                      transform={`rotate(45 ${pos.x} ${pos.y})`}
                      fill="#0c1017"
                      stroke={isSelected ? '#00f0ff' : isHovered ? '#38bdf8' : '#eab308'}
                      strokeWidth="2"
                    />
                    <rect
                      x={pos.x - 3}
                      y={pos.y - 3}
                      width="6"
                      height="6"
                      transform={`rotate(45 ${pos.x} ${pos.y})`}
                      fill={isSelected ? '#00f0ff' : '#eab308'}
                    />
                    <text
                      x={pos.x}
                      y={pos.y + 18}
                      fill={isSelected ? '#00f0ff' : '#cbd5e1'}
                      fontSize="10"
                      fontFamily="monospace"
                      textAnchor="middle"
                      className="font-semibold tracking-tight pointer-events-none drop-shadow-md"
                    >
                      {node.name}
                    </text>

                    {/* Docked pods badge */}
                    {docked.length > 0 && (
                      <g transform={`translate(${pos.x + 10}, ${pos.y - 12})`}>
                        <rect x="0" y="0" width="28" height="12" rx="3" fill="#0f172a" stroke="#38bdf8" strokeWidth="0.8" />
                        <text x="14" y="9" fill="#38bdf8" fontSize="8" fontFamily="monospace" textAnchor="middle" fontWeight="bold">
                          ⚡{docked.length}
                        </text>
                      </g>
                    )}
                  </g>
                );
              }

              if (node.node_type === 'station') {
                return (
                  <g
                    key={node.node_id}
                    className="cursor-pointer"
                    onClick={() => onSelectNode(node.node_id)}
                    onMouseEnter={() => setHoveredNode(node)}
                    onMouseLeave={() => setHoveredNode(null)}
                  >
                    {isSelected && <circle cx={pos.x} cy={pos.y} r="14" fill="none" stroke="#00f0ff" strokeWidth="1.5" className="animate-radar" />}
                    <circle
                      cx={pos.x}
                      cy={pos.y}
                      r="6"
                      fill="#0c1017"
                      stroke={isSelected ? '#00f0ff' : isHovered ? '#38bdf8' : '#00f0ff'}
                      strokeWidth="2"
                    />
                    <circle cx={pos.x} cy={pos.y} r="2.5" fill={isSelected ? '#00f0ff' : '#38bdf8'} />
                    <text
                      x={pos.x}
                      y={pos.y + 16}
                      fill={isSelected ? '#00f0ff' : '#94a3b8'}
                      fontSize="9"
                      fontFamily="monospace"
                      textAnchor="middle"
                      className="pointer-events-none drop-shadow"
                    >
                      {node.name}
                    </text>

                    {/* Docked pods badge */}
                    {docked.length > 0 && (
                      <g transform={`translate(${pos.x + 8}, ${pos.y - 12})`}>
                        <rect x="0" y="0" width="28" height="12" rx="3" fill="#0f172a" stroke="#00f0ff" strokeWidth="0.8" />
                        <text x="14" y="9" fill="#00f0ff" fontSize="8" fontFamily="monospace" textAnchor="middle" fontWeight="bold">
                          ⚡{docked.length}
                        </text>
                      </g>
                    )}
                  </g>
                );
              }

              // Intersection
              return (
                <g
                  key={node.node_id}
                  className="cursor-pointer"
                  onClick={() => onSelectNode(node.node_id)}
                  onMouseEnter={() => setHoveredNode(node)}
                  onMouseLeave={() => setHoveredNode(null)}
                >
                  <circle
                    cx={pos.x}
                    cy={pos.y}
                    r="3"
                    fill="#334155"
                    stroke={isSelected ? '#00f0ff' : '#1e293b'}
                    strokeWidth="1"
                    className="hover:stroke-slate-300"
                  />
                  {isHovered && (
                    <text
                      x={pos.x}
                      y={pos.y - 8}
                      fill="#94a3b8"
                      fontSize="8"
                      fontFamily="monospace"
                      textAnchor="middle"
                      className="pointer-events-none drop-shadow"
                    >
                      {node.name}
                    </text>
                  )}
                </g>
              );
            })}
          </g>
        </svg>
      </div>

      {/* Floating Route Switch Comparison Card */}
      {routeSwitch && (
        <div className="absolute top-14 left-4 z-20 bg-[#0c1017]/95 border border-amber-500/80 rounded-lg p-3 text-xs font-mono shadow-2xl backdrop-blur-md max-w-sm">
          <div className="flex items-center justify-between text-amber-400 font-bold border-b border-slate-800 pb-1.5 mb-2">
            <span className="flex items-center gap-1.5">
              <AlertTriangle className="w-3.5 h-3.5" />
              DYNAMIC REROUTE DEMO
            </span>
            <span className="text-[10px] text-slate-400">Edge {routeSwitch.congested_edge} Congested</span>
          </div>
          <div className="flex flex-col gap-1 text-[11px]">
            <div className="flex justify-between text-slate-300">
              <span className="text-amber-300">BEFORE (Direct):</span>
              <span className="font-bold text-amber-300">{routeSwitch.before_route.total_travel_time_min.toFixed(1)} min</span>
            </div>
            <div className="flex justify-between text-slate-300">
              <span className="text-cyan-300">AFTER (Diverted):</span>
              <span className="font-bold text-cyan-300">{routeSwitch.after_route.total_travel_time_min.toFixed(1)} min</span>
            </div>
            <div className="text-[10px] text-emerald-400 font-bold mt-1 pt-1 border-t border-slate-800/80">
              ✓ Route recalculated: avoids congestion bottleneck
            </div>
          </div>
        </div>
      )}

      {/* Floating Pod Inspector Card */}
      {activeSelectedPod && (
        <div className="absolute top-14 right-4 z-20 bg-[#0c1017]/95 border border-cyan-500/80 rounded-lg p-3 text-xs font-mono shadow-2xl backdrop-blur-md w-72">
          <div className="flex items-center justify-between border-b border-slate-800 pb-1.5 mb-2">
            <div className="flex items-center gap-2">
              <Navigation className="w-3.5 h-3.5 text-cyan-400" />
              <span className="font-bold text-cyan-300">{activeSelectedPod.pod_id}</span>
            </div>
            <button
              onClick={() => onSelectPod && onSelectPod(null)}
              className="text-slate-500 hover:text-slate-300 p-0.5 rounded"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>

          <div className="flex flex-col gap-1.5 text-[11px] text-slate-300">
            <div className="flex items-center justify-between">
              <span>Status:</span>
              <span className="px-1.5 py-0.2 rounded font-bold uppercase text-[10px] bg-slate-800 border border-slate-700 text-cyan-300">
                {activeSelectedPod.status}
              </span>
            </div>

            <div className="flex items-center justify-between">
              <span>Battery:</span>
              <div className="flex items-center gap-1.5">
                <div className="w-16 h-2 bg-slate-800 rounded overflow-hidden">
                  <div
                    className={`h-full ${
                      activeSelectedPod.battery_percent > 50
                        ? 'bg-emerald-400'
                        : activeSelectedPod.battery_percent > 20
                        ? 'bg-amber-400'
                        : 'bg-rose-500'
                    }`}
                    style={{ width: `${activeSelectedPod.battery_percent}%` }}
                  ></div>
                </div>
                <span className="font-bold text-slate-200">{activeSelectedPod.battery_percent}%</span>
              </div>
            </div>

            <div className="flex items-center justify-between">
              <span>Trip Kind:</span>
              <span className="font-semibold text-slate-200 capitalize">
                {activeSelectedPod.trip_kind} ({activeSelectedPod.occupied_seats}/4 seats)
              </span>
            </div>

            <div className="flex items-center justify-between">
              <span>Swarm:</span>
              <span className="font-bold text-emerald-400">
                {activeSelectedPod.swarm_id ? `Platoon ${activeSelectedPod.swarm_id}` : 'Solo Vehicle'}
              </span>
            </div>

            <div className="flex flex-col gap-0.5 pt-1 border-t border-slate-800/80">
              <span className="text-[10px] text-slate-500">Location:</span>
              <span className="text-[10px] text-slate-300 font-mono truncate">
                {activeSelectedPod.current_edge_id
                  ? `Edge ${activeSelectedPod.current_edge_id} (${(activeSelectedPod.edge_progress * 100).toFixed(0)}%)`
                  : `Station ${activeSelectedPod.current_node_id}`}
              </span>
            </div>

            {activeSelectedPod.route_edges.length > 0 && (
              <div className="flex flex-col gap-0.5 pt-1 border-t border-slate-800/80">
                <span className="text-[10px] text-slate-500">Route ({activeSelectedPod.route_edges.length} edges):</span>
                <span className="text-[10px] text-purple-300 font-mono truncate">
                  {activeSelectedPod.route_edges.join(' → ')}
                </span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Floating Info Tooltip on Selected / Hovered Node */}
      {(hoveredNode || (selectedNodeId && nodePositions.get(selectedNodeId))) && (
        <div className="absolute bottom-3 left-4 z-20 bg-[#0c1017]/95 border border-slate-700/80 rounded px-3 py-2 text-xs font-mono shadow-xl backdrop-blur-md max-w-sm">
          {(() => {
            const target = hoveredNode || nodePositions.get(selectedNodeId!)?.node;
            if (!target) return null;
            const d = demandMap?.nodes[target.node_id];
            const docked = dockedPodsByNode.get(target.node_id) || [];

            return (
              <div className="flex flex-col gap-1">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-cyan-300 font-bold">{target.name}</span>
                  <span className="text-[10px] text-slate-500 uppercase px-1.5 py-0.5 rounded bg-slate-800 border border-slate-700">
                    {target.node_type}
                  </span>
                </div>
                <div className="text-slate-400 text-[11px] grid grid-cols-2 gap-x-4 gap-y-0.5 mt-1">
                  <span>ID: <code className="text-slate-200">{target.node_id}</code></span>
                  <span>Coords: <span className="text-slate-200">{target.latitude.toFixed(3)}, {target.longitude.toFixed(3)}</span></span>
                  {d && (
                    <>
                      <span>Forecast Trips: <span className="text-slate-200">{d.expected_trips.toFixed(1)}</span></span>
                      <span>Balance: <span className={d.is_deficit ? 'text-rose-400 font-bold' : 'text-emerald-400 font-bold'}>{d.net_balance > 0 ? `+${d.net_balance.toFixed(1)}` : d.net_balance.toFixed(1)}</span></span>
                    </>
                  )}
                  {docked.length > 0 && (
                    <div className="col-span-2 pt-1 border-t border-slate-800 mt-0.5 text-cyan-400 font-bold">
                      Docked Pods: {docked.map((p) => p.pod_id).join(', ')}
                    </div>
                  )}
                </div>
              </div>
            );
          })()}
        </div>
      )}
    </div>
  );
};
