import type { components } from './generated/schema'

type S = components['schemas']

export type DashboardResponse = S['DashboardResponse']
export type NetworkState = S['NetworkState']
export type Plan = S['Plan']
export type RiskAssessment = S['RiskAssessment']
export type Recommendation = S['Recommendation']
export type TripwireStatus = S['TripwireStatus']
export type SystemHealth = S['SystemHealth']
export type ComponentHealth = S['ComponentHealth']
export type AutomationState = S['AutomationState']
export type SnapshotFreshness = S['SnapshotMeta']['freshness']
export type TrackedAllocation = S['TrackedAllocation']
export type RecommendationState = S['RecommendationState']
export type PlanApprovalResult = S['PlanApprovalResult']
