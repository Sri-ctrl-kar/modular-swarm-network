import React, { useRef, useEffect } from 'react';
import { SimulationEvent } from '../types/api';
import {
  Activity,
  AlertTriangle,
  RotateCw,
  Radio,
  ArrowRightLeft,
  Navigation,
  Sparkles,
  ShieldCheck,
  Zap,
} from 'lucide-react';

interface EventTimelineProps {
  events: SimulationEvent[];
}

export const EventTimeline: React.FC<EventTimelineProps> = ({ events }) => {
  const containerRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to top of newest events
  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [events]);

  const getBadgeStyle = (kind: string) => {
    switch (kind) {
      case 'CONGESTION':
        return {
          icon: AlertTriangle,
          bg: 'bg-rose-950/40 text-rose-300 border-rose-800/60',
          dot: 'bg-rose-500',
        };
      case 'ROUTE_CHANGE':
        return {
          icon: ArrowRightLeft,
          bg: 'bg-amber-950/40 text-amber-300 border-amber-800/60',
          dot: 'bg-amber-500',
        };
      case 'SWARM_FORMATION':
        return {
          icon: Radio,
          bg: 'bg-emerald-950/40 text-emerald-300 border-emerald-800/60',
          dot: 'bg-emerald-400',
        };
      case 'SWARM_SPLIT':
        return {
          icon: RotateCw,
          bg: 'bg-teal-950/40 text-teal-300 border-teal-800/60',
          dot: 'bg-teal-400',
        };
      case 'REBALANCING':
        return {
          icon: Navigation,
          bg: 'bg-purple-950/40 text-purple-300 border-purple-800/60',
          dot: 'bg-purple-400',
        };
      case 'ORCHESTRATOR':
        return {
          icon: Sparkles,
          bg: 'bg-cyan-950/40 text-cyan-300 border-cyan-800/60',
          dot: 'bg-cyan-400',
        };
      default:
        return {
          icon: Activity,
          bg: 'bg-slate-900 text-slate-300 border-slate-800',
          dot: 'bg-slate-400',
        };
    }
  };

  return (
    <div className="w-full h-full flex flex-col bg-[#0b0f17] border border-slate-800 rounded-lg overflow-hidden shadow-lg select-none">
      {/* Header */}
      <div className="bg-[#0f141f] border-b border-slate-800 px-3.5 py-2 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Activity className="w-4 h-4 text-cyan-400" />
          <span className="text-xs font-mono font-bold tracking-wider text-slate-200 uppercase">
            Live Event Feed
          </span>
        </div>
        <span className="text-[10px] font-mono text-slate-500 font-semibold">
          {events.length} events
        </span>
      </div>

      {/* Events List */}
      <div
        ref={containerRef}
        className="flex-1 p-2.5 flex flex-col gap-1.5 overflow-y-auto font-mono text-xs max-h-[350px]"
      >
        {events.length === 0 ? (
          <div className="text-slate-500 text-center py-6 text-xs italic">
            Awaiting simulation events...
          </div>
        ) : (
          events.map((evt) => {
            const badge = getBadgeStyle(evt.kind);
            const Icon = badge.icon;
            const hours = Math.floor(evt.time_min / 60);
            const mins = Math.floor(evt.time_min % 60);
            const secs = Math.floor((evt.time_min * 60) % 60);
            const timeCode = `${String(hours).padStart(2, '0')}:${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;

            return (
              <div
                key={evt.id}
                className="bg-[#121824] hover:bg-[#162030] border border-slate-800/80 rounded p-2 flex flex-col gap-1 transition-colors"
              >
                <div className="flex items-center justify-between text-[10px]">
                  <div className="flex items-center gap-1.5">
                    <span className={`w-1.5 h-1.5 rounded-full ${badge.dot}`}></span>
                    <span
                      className={`px-1.5 py-0.5 rounded text-[9px] font-bold border uppercase tracking-wider ${badge.bg}`}
                    >
                      {evt.kind}
                    </span>
                  </div>
                  <span className="text-slate-500">{timeCode}</span>
                </div>

                <div className="text-[11px] text-slate-300 font-sans pl-3 leading-snug">
                  {evt.message}
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};
