import orderListManifest from "../../../src/agent_modules/ecommerce/presentation/ecommerce_order_list_v1.json";
import logisticsManifest from "../../../src/agent_modules/ecommerce/presentation/ecommerce_logistics_overview_v1.json";
import businessStatusManifest from "../../../src/agent_modules/ecommerce/presentation/ecommerce_business_status_list_v1.json";
import nextActionsManifest from "../../../src/agent_modules/ecommerce/presentation/ecommerce_next_actions_v1.json";
import eligibilityDecisionManifest from "../../../src/agent_modules/ecommerce/presentation/ecommerce_eligibility_decision_v1.json";
import advisoryManifest from "../../../src/agent_modules/ecommerce/presentation/ecommerce_advisory_v1.json";
import transactionStatusManifest from "../../../src/agent_core/presentation/contracts/runtime_transaction_status_v1.json";
import interactionTimelineManifest from "../../../src/agent_core/presentation/contracts/runtime_interaction_timeline_v1.json";
import resourceListManifest from "../../../src/agent_core/presentation/contracts/runtime_resource_list_v1.json";

const manifests = [
  orderListManifest,
  logisticsManifest,
  businessStatusManifest,
  nextActionsManifest,
  eligibilityDecisionManifest,
  advisoryManifest,
  transactionStatusManifest,
  interactionTimelineManifest,
  resourceListManifest,
];

export const PRESENTATION_MANIFESTS = Object.freeze(
  Object.fromEntries(manifests.map((manifest) => [manifest.contract_id, manifest]))
);

export const WEB_RENDERERS = Object.freeze(
  Object.fromEntries(
    manifests.map((manifest) => [manifest.contract_id, manifest.renderer?.web || ""])
  )
);

function missing(value) {
  return value === null || value === undefined || (typeof value === "string" && !value.trim());
}

function pathValue(target, path) {
  return String(path).split(".").reduce((current, key) => (
    current && typeof current === "object" ? current[key] : undefined
  ), target);
}

function validateMap(target, map, missingSemantics) {
  for (const [field, semantic] of Object.entries(map || {})) {
    if (missing(pathValue(target, field))) missingSemantics.push(semantic);
  }
}

function validateCollection(block, key, fieldKey, shapeSemantic, missingSemantics) {
  const map = block.__manifest?.payload?.[fieldKey] || {};
  if (!Object.keys(map).length) return;
  const rows = block[key];
  if (!Array.isArray(rows)) {
    missingSemantics.push(shapeSemantic);
    return;
  }
  for (const row of rows) {
    if (!row || typeof row !== "object") {
      missingSemantics.push(`${shapeSemantic}_item`);
      continue;
    }
    validateMap(row, map, missingSemantics);
  }
}

function validateCoverage(block, manifest, missingSemantics) {
  const declared = manifest.coverage || {};
  const coverage = block.coverage;
  if (!coverage || typeof coverage !== "object") {
    missingSemantics.push("coverage");
    return;
  }
  if (coverage.mode !== declared.mode) missingSemantics.push("coverage_mode");
  if (coverage.source_population !== declared.source_population) missingSemantics.push("coverage_source_population");
  if (declared.mode === "full") {
    if (coverage.status !== "complete") missingSemantics.push("coverage_complete");
    if (!Number.isInteger(coverage.resolved_member_count)) missingSemantics.push("coverage_resolved_member_count");
    if (!Number.isInteger(coverage.presented_member_count)) missingSemantics.push("coverage_presented_member_count");
    if (Number.isInteger(coverage.resolved_member_count) && Number.isInteger(coverage.presented_member_count) && coverage.resolved_member_count !== coverage.presented_member_count) {
      missingSemantics.push("coverage_population_mismatch");
    }
    if (Array.isArray(block.items) && Number.isInteger(coverage.presented_member_count) && block.items.length !== coverage.presented_member_count) {
      missingSemantics.push("coverage_rendered_item_count");
    }
  } else if (declared.mode === "not_collection" && coverage.status !== "not_applicable") {
    missingSemantics.push("coverage_not_applicable");
  }
}

/** Strict browser-side mirror of the server release contract. No aliases. */
export function validatePresentationBlock(block) {
  const missingSemantics = [];
  if (!block || typeof block !== "object") {
    return { valid: false, missingSemantics: ["presentation_block"], manifest: null, rendererId: "" };
  }
  const manifest = PRESENTATION_MANIFESTS[block.contract_id];
  if (!manifest) {
    return { valid: false, missingSemantics: ["registered_presentation_contract"], manifest: null, rendererId: "" };
  }
  if (block.contract_version !== manifest.version) missingSemantics.push("contract_version");
  if (block.contract_owner !== manifest.contract_owner) missingSemantics.push("contract_owner");
  if (block.projection_boundary !== manifest.projection_boundary) missingSemantics.push("projection_boundary");
  if (block.type !== manifest.payload?.block_type) missingSemantics.push("block_type");
  validateMap(block, manifest.payload?.block_required_fields || {}, missingSemantics);
  validateCollection({ ...block, __manifest: manifest }, "items", "item_required_fields", "result_items", missingSemantics);
  validateCollection({ ...block, __manifest: manifest }, "actions", "action_required_fields", "result_actions", missingSemantics);
  validateCoverage(block, manifest, missingSemantics);
  const rendererId = WEB_RENDERERS[manifest.contract_id] || "";
  if (!rendererId) missingSemantics.push("registered_channel_renderer");
  return {
    valid: missingSemantics.length === 0,
    missingSemantics: [...new Set(missingSemantics)],
    manifest,
    rendererId,
  };
}
