import http from 'k6/http';
import {check} from 'k6';
import exec from 'k6/execution';

export const options = {
    scenarios: {
        issue: {
            executor: 'constant-arrival-rate',
            rate: Number(__ENV.RATE || 100),
            timeUnit: '1s',
            duration: __ENV.DURATION || '30s',
            preAllocatedVUs: 500,
            maxVUs: 1000,
        },
    },
    summaryTrendStats: ['avg', 'p(50)', 'p(95)', 'p(99)', 'max'],
};

export default function () {
    const res = http.post(
        'http://localhost:8000/coupons/1/issue',
        JSON.stringify({user_id: exec.scenario.iterationInTest}),
        {headers: {'Content-Type': 'application/json'}},
    );
    check(res, {'status is 201': (r) => r.status === 201});
}