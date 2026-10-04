import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';
import { CityMap } from '../map/CityMap';
import { NetworkTopology } from '../types/api';

const mockTopology: NetworkTopology = {
  node_count: 3,
  edge_count: 2,
  bounds: {
    min_lat: 44.9,
    max_lat: 45.1,
    min_lon: 9.9,
    max_lon: 10.1,
  },
  nodes: [
    { node_id: 'north_station', name: 'North Station', latitude: 45.06, longitude: 10.0, node_type: 'station' },
    { node_id: 'airport', name: 'Airport', latitude: 44.95, longitude: 10.05, node_type: 'terminal' },
    { node_id: 'jct_1', name: 'Junction 1', latitude: 45.0, longitude: 10.02, node_type: 'intersection' },
  ],
  edges: [
    {
      edge_id: 'E001',
      source: 'north_station',
      destination: 'jct_1',
      distance_km: 5.2,
      base_travel_time_min: 4.1,
      capacity_vehicles_per_hour: 1500,
      road_type: 'arterial',
      current_vehicle_count: 300,
      current_travel_time_min: 4.1,
      utilization: 0.2,
    },
    {
      edge_id: 'E002',
      source: 'jct_1',
      destination: 'airport',
      distance_km: 8.4,
      base_travel_time_min: 6.3,
      capacity_vehicles_per_hour: 3600,
      road_type: 'trunk',
      current_vehicle_count: 1200,
      current_travel_time_min: 6.3,
      utilization: 0.33,
    },
  ],
};

describe('CityMap component', () => {
  it('renders station and terminal nodes on map', () => {
    render(
      <CityMap
        topology={mockTopology}
        pods={[]}
        swarms={[]}
        edgeCongestions={{}}
        demandMap={null}
        routeSwitch={null}
        viewMode="TRAFFIC"
        onViewModeChange={vi.fn()}
        selectedNodeId={null}
        onSelectNode={vi.fn()}
      />
    );

    expect(screen.getByText('North Station')).toBeInTheDocument();
    expect(screen.getByText('Airport')).toBeInTheDocument();
  });

  it('switches view mode when tabs are clicked', () => {
    const onViewModeChange = vi.fn();

    render(
      <CityMap
        topology={mockTopology}
        pods={[]}
        swarms={[]}
        edgeCongestions={{}}
        demandMap={null}
        routeSwitch={null}
        viewMode="TRAFFIC"
        onViewModeChange={onViewModeChange}
        selectedNodeId={null}
        onSelectNode={vi.fn()}
      />
    );

    fireEvent.click(screen.getByText('DEMAND'));
    expect(onViewModeChange).toHaveBeenCalledWith('DEMAND');

    fireEvent.click(screen.getByText('SWARMS'));
    expect(onViewModeChange).toHaveBeenCalledWith('SWARMS');
  });

  it('renders swarm platoon formation and leader badge', () => {
    const mockPods = [
      {
        pod_id: 'POD_01',
        current_node_id: 'north_station',
        current_edge_id: 'E001',
        status: 'traveling' as const,
        battery_percent: 92,
        occupied_seats: 2,
        trip_kind: 'passenger' as const,
        edge_progress: 0.5,
        swarm_id: 'SW001',
        route_edges: ['E001', 'E002'],
        route_index: 0,
      },
      {
        pod_id: 'POD_02',
        current_node_id: 'north_station',
        current_edge_id: 'E001',
        status: 'traveling' as const,
        battery_percent: 88,
        occupied_seats: 1,
        trip_kind: 'passenger' as const,
        edge_progress: 0.5,
        swarm_id: 'SW001',
        route_edges: ['E001', 'E002'],
        route_index: 0,
      },
    ];

    const mockSwarms = [
      {
        swarm_id: 'SW001',
        status: 'active' as const,
        member_pod_ids: ['POD_01', 'POD_02'],
        origin_node_id: 'north_station',
        divergence_node_id: 'airport',
        corridor_edges: ['E001', 'E002'],
        current_edge_id: 'E001',
        shared_distance_km: 13.6,
        corridor_distance_km: 13.6,
      },
    ];

    render(
      <CityMap
        topology={mockTopology}
        pods={mockPods}
        swarms={mockSwarms}
        edgeCongestions={{}}
        demandMap={null}
        routeSwitch={null}
        viewMode="SWARMS"
        onViewModeChange={vi.fn()}
        selectedNodeId={null}
        onSelectNode={vi.fn()}
      />
    );

    expect(screen.getByText('●━━● PLATOON SW001')).toBeInTheDocument();
  });

  it('renders pod inspector when pod is selected', () => {
    const mockPods = [
      {
        pod_id: 'POD_01',
        current_node_id: 'north_station',
        current_edge_id: 'E001',
        status: 'traveling' as const,
        battery_percent: 92,
        occupied_seats: 3,
        trip_kind: 'passenger' as const,
        edge_progress: 0.45,
        swarm_id: 'SW001',
        route_edges: ['E001', 'E002'],
        route_index: 0,
      },
    ];

    render(
      <CityMap
        topology={mockTopology}
        pods={mockPods}
        swarms={[]}
        edgeCongestions={{}}
        demandMap={null}
        routeSwitch={null}
        viewMode="TRAFFIC"
        onViewModeChange={vi.fn()}
        selectedNodeId={null}
        onSelectNode={vi.fn()}
        selectedPodId="POD_01"
      />
    );

    expect(screen.getAllByText('POD_01').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('92%')).toBeInTheDocument();
    expect(screen.getByText('Platoon SW001')).toBeInTheDocument();
  });
});
