#!/usr/bin/env node
// Prints shell variable assignments derived from contracts/services.json, so the
// deploy script takes scaling bounds, service accounts, and resource names from
// the contract instead of repeating them.
//
//   eval "$(node tools/deploy-vars.mjs)"

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const catalog = JSON.parse(fs.readFileSync(path.join(repoRoot, 'contracts', 'services.json'), 'utf8'));
const svc = (name) => catalog.services.find((s) => s.name === name);
const resource = (kind) => catalog.resources.find((r) => r.kind === kind).name;
const envDefault = (service, name) => service.env.find((e) => e.name === name).default;

const planner = svc('planner');
const job = svc('ingestion-job');

const vars = {
  PLANNER_NAME: planner.name,
  PLANNER_PATH: planner.path,
  PLANNER_SA: planner.iam.service_account,
  PLANNER_ROLES: planner.iam.roles.join(' '),
  PLANNER_MIN_INSTANCES: planner.scaling.min_instances,
  PLANNER_MAX_INSTANCES: planner.scaling.max_instances,
  PLANNER_CONCURRENCY: planner.scaling.concurrency,
  PLANNER_CPU: planner.scaling.cpu,
  PLANNER_MEMORY: planner.scaling.memory,
  PLANNER_TIMEOUT: planner.scaling.timeout_seconds,
  JOB_NAME: job.name,
  JOB_PATH: job.path,
  JOB_SA: job.iam.service_account,
  JOB_ROLES: job.iam.roles.join(' '),
  JOB_TASKS: job.scaling.tasks,
  JOB_PARALLELISM: job.scaling.parallelism,
  JOB_MAX_RETRIES: job.scaling.max_retries,
  JOB_CPU: job.scaling.cpu,
  JOB_MEMORY: job.scaling.memory,
  JOB_TASK_TIMEOUT: job.scaling.task_timeout_seconds,
  JOB_MAX_OBJECTS: envDefault(job, 'MAX_OBJECTS'),
  JOB_REFRESH_AFTER_HOURS: envDefault(job, 'REFRESH_AFTER_HOURS'),
  SCHEDULER_SA: job.iam.invokers[0].replace(/^serviceAccount:/, ''),
  PUBSUB_TOPIC: resource('pubsub-topic'),
  PUBSUB_SUBSCRIPTION: resource('pubsub-subscription'),
  SCHEDULER_JOB: resource('cloud-scheduler-job'),
};

for (const [name, value] of Object.entries(vars)) {
  console.log(`${name}='${String(value).replace(/'/g, `'\\''`)}'`);
}
