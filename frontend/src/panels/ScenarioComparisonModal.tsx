import React, { useEffect, useState } from 'react';
import { X, BarChart3, Activity, Check, Shield, Info, ArrowUpRight, ArrowDownRight } from 'lucide-react';
import { ComparisonResult } from '../types/api';
import { api } from '../api/client';

interface ScenarioComparisonModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export const ScenarioComparisonModal: React.FC<ScenarioComparisonModalProps> = ({
  isOpen,
  onClose,
}) => {
  const [data, setData] = useState<ComparisonResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen && !data) {
      setLoading(true);
      setError(null);
      api
        .getComparison()
        .then((res) => {
          setData(res);
          setLoading(false);
        })
        .catch((err) => {
          setError(err.message || 'Failed to load comparison');
          setLoading(false);
        });
    }
  }, [isOpen, data]);

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4 font-mono select-none">
      <div className="bg-[#0b0f17] border border-slate-700 rounded-lg max-w-4xl w-full max-h-[90vh] flex flex-col shadow-2xl overflow-hidden animate-in fade-in zoom-in-95 duration-200">
        {/* Header */}
        <div className="bg-[#0f141f] border-b border-slate-800 px-5 py-3.5 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <BarChart3 className="w-5 h-5 text-cyan-400" />
            <div>
              <h2 className="text-sm font-bold text-slate-100 uppercase tracking-wider">
                Controlled Milestone Comparison
              </h2>
              <p className="text-[10px] text-slate-400 font-sans">
                Identical Seed (42), City Network, Fleet Size (60), Demand (400 passengers), 180m Horizon
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-slate-800 text-slate-400 hover:text-slate-200 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="p-5 flex-1 overflow-y-auto flex flex-col gap-5 text-xs">
          {loading ? (
            <div className="flex flex-col items-center justify-center py-16 gap-3 text-slate-400">
              <Activity className="w-8 h-8 animate-spin text-cyan-400" />
              <span>Running deterministic comparison across M3, M4, and M5/M6 engines...</span>
            </div>
          ) : error ? (
            <div className="p-4 bg-rose-950/40 border border-rose-800 rounded text-rose-300">
              {error}
            </div>
          ) : data ? (
            <>
              {/* Summary Cards */}
              <div className="grid grid-cols-3 gap-3">
                <div className="bg-[#121824] border border-slate-800 rounded p-3 flex flex-col justify-between">
                  <span className="text-[10px] text-slate-500 uppercase">Wait Time Reduction</span>
                  <div className="text-xl font-bold text-emerald-400 flex items-center gap-1 mt-1">
                    <ArrowDownRight className="w-5 h-5" />
                    {data.impact_summary.wait_time_reduction_pct.toFixed(1)}%
                  </div>
                  <span className="text-[10px] text-slate-400 mt-1">
                    Adaptive fleet positioning vs baseline
                  </span>
                </div>

                <div className="bg-[#121824] border border-slate-800 rounded p-3 flex flex-col justify-between">
                  <span className="text-[10px] text-slate-500 uppercase">Road Space Freed</span>
                  <div className="text-xl font-bold text-cyan-400 flex items-center gap-1 mt-1">
                    <ArrowDownRight className="w-5 h-5" />
                    {data.impact_summary.road_space_saved_equiv_km.toFixed(1)}{' '}
                    <span className="text-xs font-normal text-slate-400">equiv-km</span>
                  </div>
                  <span className="text-[10px] text-slate-400 mt-1">
                    Via 0.4 headway swarm platooning
                  </span>
                </div>

                <div className="bg-[#121824] border border-slate-800 rounded p-3 flex flex-col justify-between">
                  <span className="text-[10px] text-slate-500 uppercase">Additional Trips Served</span>
                  <div className="text-xl font-bold text-amber-400 flex items-center gap-1 mt-1">
                    <ArrowUpRight className="w-5 h-5" />
                    +{data.impact_summary.trips_served_gain}
                  </div>
                  <span className="text-[10px] text-slate-400 mt-1">
                    Deadhead cost: {data.impact_summary.deadhead_pct.toFixed(1)}% of distance
                  </span>
                </div>
              </div>

              {/* Side-by-Side Detailed Metric Table */}
              <div className="border border-slate-800 rounded-lg overflow-hidden">
                <table className="w-full text-left border-collapse">
                  <thead>
                    <tr className="bg-[#0f141f] border-b border-slate-800 text-[10px] uppercase text-slate-400 tracking-wider">
                      <th className="p-3">Simulation Metric</th>
                      <th className="p-3 text-right">Baseline (M3)</th>
                      <th className="p-3 text-right">Swarm (M4)</th>
                      <th className="p-3 text-right text-cyan-300">Adaptive + Gemini (M5/M6)</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/70 text-slate-300">
                    <tr className="hover:bg-slate-900/40">
                      <td className="p-3 font-semibold text-slate-200">Trips Served</td>
                      <td className="p-3 text-right">{data.baseline.trips_served}</td>
                      <td className="p-3 text-right">{data.swarm.trips_served}</td>
                      <td className="p-3 text-right font-bold text-cyan-300">{data.adaptive.trips_served}</td>
                    </tr>
                    <tr className="hover:bg-slate-900/40">
                      <td className="p-3 font-semibold text-slate-200">Unserved Requests</td>
                      <td className="p-3 text-right text-rose-400">{data.baseline.unserved_trips}</td>
                      <td className="p-3 text-right text-rose-400">{data.swarm.unserved_trips}</td>
                      <td className="p-3 text-right font-bold text-emerald-400">{data.adaptive.unserved_trips}</td>
                    </tr>
                    <tr className="hover:bg-slate-900/40">
                      <td className="p-3 font-semibold text-slate-200">Average Wait Time</td>
                      <td className="p-3 text-right">
                        {data.baseline.average_wait_min ? `${data.baseline.average_wait_min.toFixed(2)} min` : '—'}
                      </td>
                      <td className="p-3 text-right">
                        {data.swarm.average_wait_min ? `${data.swarm.average_wait_min.toFixed(2)} min` : '—'}
                      </td>
                      <td className="p-3 text-right font-bold text-emerald-400">
                        {data.adaptive.average_wait_min ? `${data.adaptive.average_wait_min.toFixed(2)} min` : '—'}
                      </td>
                    </tr>
                    <tr className="hover:bg-slate-900/40">
                      <td className="p-3 font-semibold text-slate-200">Average Completion Time</td>
                      <td className="p-3 text-right">
                        {data.baseline.average_completion_time_min ? `${data.baseline.average_completion_time_min.toFixed(2)} min` : '—'}
                      </td>
                      <td className="p-3 text-right">
                        {data.swarm.average_completion_time_min ? `${data.swarm.average_completion_time_min.toFixed(2)} min` : '—'}
                      </td>
                      <td className="p-3 text-right">
                        {data.adaptive.average_completion_time_min ? `${data.adaptive.average_completion_time_min.toFixed(2)} min` : '—'}
                      </td>
                    </tr>
                    <tr className="hover:bg-slate-900/40">
                      <td className="p-3 font-semibold text-slate-200">Pod Total Distance</td>
                      <td className="p-3 text-right">{data.baseline.pod_distance_km.toFixed(1)} km</td>
                      <td className="p-3 text-right">{data.swarm.pod_distance_km.toFixed(1)} km</td>
                      <td className="p-3 text-right">{data.adaptive.pod_distance_km.toFixed(1)} km</td>
                    </tr>
                    <tr className="hover:bg-slate-900/40">
                      <td className="p-3 font-semibold text-slate-200">Deadhead Distance (Empty)</td>
                      <td className="p-3 text-right">0.0 km</td>
                      <td className="p-3 text-right">0.0 km</td>
                      <td className="p-3 text-right text-amber-400 font-bold">{data.adaptive.deadhead_distance_km.toFixed(1)} km</td>
                    </tr>
                    <tr className="hover:bg-slate-900/40">
                      <td className="p-3 font-semibold text-slate-200">Total Battery Energy</td>
                      <td className="p-3 text-right">{data.baseline.total_energy_kwh.toFixed(1)} kWh</td>
                      <td className="p-3 text-right">{data.swarm.total_energy_kwh.toFixed(1)} kWh</td>
                      <td className="p-3 text-right">{data.adaptive.total_energy_kwh.toFixed(1)} kWh</td>
                    </tr>
                    <tr className="hover:bg-slate-900/40">
                      <td className="p-3 font-semibold text-slate-200">Final Forecast Deficit</td>
                      <td className="p-3 text-right text-rose-400">{data.baseline.final_total_deficit.toFixed(1)}</td>
                      <td className="p-3 text-right text-rose-400">{data.swarm.final_total_deficit.toFixed(1)}</td>
                      <td className="p-3 text-right font-bold text-emerald-400">{data.adaptive.final_total_deficit.toFixed(1)}</td>
                    </tr>
                  </tbody>
                </table>
              </div>

              {/* Integrity Notice */}
              <div className="p-3 bg-slate-900/80 border border-slate-800 rounded flex flex-col gap-1 text-[11px] text-slate-400 font-sans">
                <span className="font-mono text-cyan-400 text-[10px] font-bold uppercase">
                  {data.provenance}
                </span>
                <p>
                  Comparing two modes reflects full fleet interaction rather than an isolated vehicle effect. Swarm pods wait up to formation delay threshold before departing, and empty rebalancing introduces honest deadhead mileage without concealing energy or road occupancy costs.
                </p>
              </div>
            </>
          ) : null}
        </div>

        {/* Footer */}
        <div className="bg-[#0f141f] border-t border-slate-800 px-5 py-3 flex justify-end">
          <button
            onClick={onClose}
            className="px-4 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded text-xs font-mono font-medium transition-colors"
          >
            CLOSE
          </button>
        </div>
      </div>
    </div>
  );
};
