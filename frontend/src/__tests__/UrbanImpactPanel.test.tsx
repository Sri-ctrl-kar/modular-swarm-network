import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import React from 'react';
import { UrbanImpactPanel } from '../panels/UrbanImpactPanel';
import { SimulationMetrics } from '../types/api';

const mockMetrics: SimulationMetrics = {
  time_min: 180.0,
  tick_index: 360,
  is_finished: false,
  total_pods: 60,
  active_pods: 30,
  idle_pods: 25,
  charging_pods: 5,
  active_swarms: 4,
  total_swarms_formed: 18,
  trips_served: 240,
  unserved_trips: 10,
  average_wait_min: 2.8,
  average_completion_time_min: 12.5,
  total_energy_kwh: 210.4,
  pod_distance_km: 1500.0,
  deadhead_distance_km: 150.0,
  deadhead_ratio: 0.1,
  passenger_distance_km: 1350.0,
  road_occupancy_equiv_km: 1250.0,
  road_occupancy_saved_equiv_km: 250.0,
  completed_repositions: 12,
  final_total_deficit: 1.5,
};

describe('UrbanImpactPanel component', () => {
  it('renders measured deterministic metrics and scenario potential disclaimer', () => {
    render(<UrbanImpactPanel metrics={mockMetrics} />);

    // Measured road space
    expect(screen.getByText(/250.0/i)).toBeInTheDocument();
    expect(screen.getByText(/16.7% of fleet footprint freed/i)).toBeInTheDocument();

    // Deadhead
    expect(screen.getByText('10.0%')).toBeInTheDocument();

    // Planning assumption label
    expect(screen.getByText(/SCENARIO POTENTIAL — PLANNING ASSUMPTION/i)).toBeInTheDocument();
    expect(screen.getByText(/up to 40% urban road space reclaimed/i)).toBeInTheDocument();
  });
});
