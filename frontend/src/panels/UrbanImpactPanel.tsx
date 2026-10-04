import React from 'react';
import { SimulationMetrics } from '../types/api';
import { Minimize2, TrendingUp, AlertTriangle, Leaf, Zap, ShieldAlert, Info } from 'lucide-react';

interface UrbanImpactPanelProps {
  metrics: SimulationMetrics | null;
}

export const UrbanImpactPanel: React.FC<UrbanImpactPanelProps> = ({ metrics }) => {
  if (!metrics) return null;

  // Actual simulation metrics
  const roadSpaceSaved = metrics.road_occupancy_saved_equiv_km;
  const deadheadPct = (metrics.deadhead_ratio * 100).toFixed(1);
  const roadSavingPct =
    metrics.pod_distance_km > 0
      ? ((roadSpaceSaved / metrics.pod_distance_km) * 100).toFixed(1)
      : '0.0';

  return (
    <div className="w-full bg-[#0b0f17] border border-slate-800 rounded-lg p-3.5 flex flex-col gap-3 shadow-lg select-none font-mono text-xs">
      <div className="flex items-center justify-between border-b border-slate-800 pb-2">
        <div className="flex items-center gap-2">
          <Leaf className="w-4 h-4 text-emerald-400" />
          <span className="font-bold tracking-wider text-slate-200 uppercase">
            Urban Mobility Impact
          </span>
        </div>
        <span className="text-[10px] text-slate-500 uppercase tracking-widest">
          Deterministic Metrics
        </span>
      </div>

      {/* Grid of Measured Deterministic Impacts */}
      <div className="grid grid-cols-2 gap-2.5">
        <div className="bg-[#121824] border border-slate-800 p-2.5 rounded flex flex-col justify-between">
          <div className="flex items-center justify-between text-[10px] text-slate-400">
            <span>ROAD SPACE SAVED</span>
            <Minimize2 className="w-3 h-3 text-emerald-400" />
          </div>
          <div className="text-base font-bold text-emerald-400 mt-1">
            {roadSpaceSaved.toFixed(1)}{' '}
            <span className="text-[10px] font-normal text-slate-400">equiv-km</span>
          </div>
          <div className="text-[10px] text-slate-500 mt-0.5">
            {roadSavingPct}% of fleet footprint freed
          </div>
        </div>

        <div className="bg-[#121824] border border-slate-800 p-2.5 rounded flex flex-col justify-between">
          <div className="flex items-center justify-between text-[10px] text-slate-400">
            <span>DEADHEAD RATIO</span>
            <TrendingUp className="w-3 h-3 text-cyan-400" />
          </div>
          <div className="text-base font-bold text-slate-200 mt-1">
            {deadheadPct}%
          </div>
          <div className="text-[10px] text-slate-500 mt-0.5">
            {metrics.deadhead_distance_km.toFixed(1)} km empty driving
          </div>
        </div>

        <div className="bg-[#121824] border border-slate-800 p-2.5 rounded flex flex-col justify-between">
          <div className="flex items-center justify-between text-[10px] text-slate-400">
            <span>TRIPS FULFILLED</span>
            <Zap className="w-3 h-3 text-amber-400" />
          </div>
          <div className="text-base font-bold text-amber-400 mt-1">
            {metrics.trips_served}{' '}
            <span className="text-[10px] font-normal text-slate-400">
              ({metrics.unserved_trips} pending)
            </span>
          </div>
          <div className="text-[10px] text-slate-500 mt-0.5">
            Avg wait: {metrics.average_wait_min !== null ? `${metrics.average_wait_min.toFixed(1)}m` : 'N/A'}
          </div>
        </div>

        <div className="bg-[#121824] border border-slate-800 p-2.5 rounded flex flex-col justify-between">
          <div className="flex items-center justify-between text-[10px] text-slate-400">
            <span>TOTAL ENERGY</span>
            <Leaf className="w-3 h-3 text-emerald-400" />
          </div>
          <div className="text-base font-bold text-slate-200 mt-1">
            {metrics.total_energy_kwh.toFixed(1)}{' '}
            <span className="text-[10px] font-normal text-slate-400">kWh</span>
          </div>
          <div className="text-[10px] text-slate-500 mt-0.5">
            From M3 deterministic battery model
          </div>
        </div>
      </div>

      {/* Explicit SCENARIO POTENTIAL Notice (Section 15 Compliance) */}
      <div className="p-2.5 bg-slate-900/90 border border-slate-800 rounded flex flex-col gap-1 text-[11px]">
        <div className="flex items-center gap-1.5 text-amber-400 font-bold text-[10px] uppercase tracking-wider">
          <Info className="w-3.5 h-3.5" />
          <span>SCENARIO POTENTIAL — PLANNING ASSUMPTION</span>
        </div>
        <p className="text-slate-400 font-sans leading-relaxed text-[11px]">
          Target estimates such as <strong className="text-slate-200">up to 40% urban road space reclaimed</strong> are prospective planning models predicated on theoretical 0.4 headway compression across all arterial corridors. They are not measured empirical claims of this discrete synthetic run.
        </p>
      </div>
    </div>
  );
};
