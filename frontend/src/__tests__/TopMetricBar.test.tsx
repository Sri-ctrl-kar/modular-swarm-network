import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import React from 'react';
import { TopMetricBar } from '../panels/TopMetricBar';
import { SimulationMetrics } from '../types/api';

const mockMetrics: SimulationMetrics = {
  time_min: 42.5,
  tick_index: 85,
  is_finished: false,
  total_pods: 60,
  active_pods: 48,
  idle_pods: 8,
  charging_pods: 4,
  active_swarms: 5,
  total_swarms_formed: 12,
  trips_served: 142,
  unserved_trips: 18,
  average_wait_min: 3.42,
  average_completion_time_min: 14.18,
  total_energy_kwh: 128.65,
  pod_distance_km: 842.1,
  deadhead_distance_km: 98.4,
  deadhead_ratio: 0.1168,
  passenger_distance_km: 743.7,
  road_occupancy_equiv_km: 712.4,
  road_occupancy_saved_equiv_km: 129.7,
  completed_repositions: 6,
  final_total_deficit: 4.2,
};

describe('TopMetricBar component', () => {
  it('renders loading state when metrics is null', () => {
    render(<TopMetricBar metrics={null} />);
    expect(screen.getByText(/loading simulation metrics/i)).toBeInTheDocument();
  });

  it('renders real simulation metrics accurately', () => {
    render(<TopMetricBar metrics={mockMetrics} />);

    // PODS
    expect(screen.getByText('48/60')).toBeInTheDocument();

    // SWARMS
    expect(screen.getByText('5')).toBeInTheDocument();

    // TRIPS SERVED
    expect(screen.getByText('142')).toBeInTheDocument();

    // AVG WAIT
    expect(screen.getByText('3.4m')).toBeInTheDocument();

    // ENERGY
    expect(screen.getByText('128.7')).toBeInTheDocument();

    // DEADHEAD
    expect(screen.getByText('11.7%')).toBeInTheDocument();

    // ROAD SPACE
    expect(screen.getByText('712.4')).toBeInTheDocument();
  });
});
