import React from 'react';
import { SimulationMetrics } from '../types/api';
import { Navigation, Users, CheckCircle2, Clock, Zap, Percent, Minimize2, Radio } from 'lucide-react';

interface TopMetricBarProps {
  metrics: SimulationMetrics | null;
}

export const TopMetricBar: React.FC<TopMetricBarProps> = ({ metrics }) => {
  if (!metrics) {
    return (
      <div className="w-full bg-[#0a0e16] border-b border-slate-800/80 px-4 py-2 flex items-center justify-between text-xs font-mono text-slate-500">
        Loading simulation metrics...
      </div>
    );
  }

  const items = [
    {
      label: 'PODS',
      value: `${metrics.active_pods}/${metrics.total_pods}`,
      sub: `${metrics.idle_pods} idle · ${metrics.charging_pods} chg`,
      icon: Navigation,
      color: 'text-cyan-400',
    },
    {
      label: 'SWARMS',
      value: `${metrics.active_swarms}`,
      sub: `${metrics.total_swarms_formed} formed total`,
      icon: Radio,
      color: 'text-emerald-400',
    },
    {
      label: 'TRIPS SERVED',
      value: `${metrics.trips_served}`,
      sub: `${metrics.unserved_trips} unserved`,
      icon: CheckCircle2,
      color: 'text-cyan-300',
    },
    {
      label: 'AVG WAIT',
      value: metrics.average_wait_min !== null ? `${metrics.average_wait_min.toFixed(1)}m` : 'N/A',
      sub: 'request to board',
      icon: Clock,
      color: 'text-slate-200',
    },
    {
      label: 'AVG COMPLETION',
      value: metrics.average_completion_time_min !== null ? `${metrics.average_completion_time_min.toFixed(1)}m` : 'N/A',
      sub: 'door to door',
      icon: Clock,
      color: 'text-slate-200',
    },
    {
      label: 'ENERGY',
      value: `${metrics.total_energy_kwh.toFixed(1)}`,
      sub: 'kWh consumed',
      icon: Zap,
      color: 'text-amber-400',
    },
    {
      label: 'DEADHEAD',
      value: `${(metrics.deadhead_ratio * 100).toFixed(1)}%`,
      sub: `${metrics.deadhead_distance_km.toFixed(1)} empty km`,
      icon: Percent,
      color: metrics.deadhead_ratio > 0.3 ? 'text-amber-400' : 'text-slate-300',
    },
    {
      label: 'ROAD SPACE',
      value: `${metrics.road_occupancy_equiv_km.toFixed(1)}`,
      sub: `${metrics.road_occupancy_saved_equiv_km.toFixed(1)} equiv-km saved`,
      icon: Minimize2,
      color: 'text-emerald-400',
    },
  ];

  return (
    <div className="w-full bg-[#0a0e16] border-b border-slate-800/80 px-4 py-2 overflow-x-auto select-none">
      <div className="grid grid-cols-4 lg:grid-cols-8 gap-2 min-w-[750px]">
        {items.map((item, idx) => {
          const Icon = item.icon;
          return (
            <div
              key={idx}
              className="bg-[#0f141f] border border-slate-800/90 rounded px-2.5 py-1.5 flex flex-col justify-between hover:border-slate-700 transition-colors"
            >
              <div className="flex items-center justify-between text-[10px] font-mono tracking-wider text-slate-500 uppercase">
                <span>{item.label}</span>
                <Icon className={`w-3 h-3 ${item.color} opacity-80`} />
              </div>
              <div className="flex items-baseline gap-1.5 mt-0.5">
                <span className={`text-sm lg:text-base font-mono font-bold tracking-tight ${item.color}`}>
                  {item.value}
                </span>
              </div>
              <div className="text-[9px] font-mono text-slate-400 truncate mt-0.5">
                {item.sub}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};
