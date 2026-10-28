import http from 'k6/http';
import { check } from 'k6';

// Intentionally failing requests remain visible during a fault. Recovery is verified
// independently by Zenkai over an observation window while this workload continues.
const rate = Math.min(60, Math.max(1, Number(__ENV.RATE || 25)));
export const options = {
  scenarios: { campus: { executor: 'constant-arrival-rate', rate, timeUnit: '1s',
    duration: __ENV.DURATION || '3m', preAllocatedVUs: 30, maxVUs: 80 } },
  thresholds: { dropped_iterations: ['count==0'] },
  tags: { application: 'zenkai-campus', test_kind: 'controlled-demo' },
};

export default function () {
  const response = http.get(`${__ENV.TARGET_URL || 'http://gateway'}/api/portal`, { timeout: '4s', tags: { name: 'campus_portal' } });
  check(response, {
    'portal returns correct content': (r) => {
      try { return r.status === 200 && r.json('signature') === 'northbridge-portal-v1'; }
      catch { return false; }
    },
  });
}

export function handleSummary(data) {
  return { '/results/latest.json': JSON.stringify(data, null, 2),
    stdout: `\nZenkai load complete. Failed checks include the intentional outage. Full results: /results/latest.json\n` };
}
