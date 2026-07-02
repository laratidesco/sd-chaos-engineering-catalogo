import http from "k6/http";
import { check, sleep } from "k6";

const BASE_URL = __ENV.BASE_URL || "http://localhost:8080";
const DURATION = __ENV.LOAD_DURATION || "180s";
const TIMEOUT = "10s";

export const options = {
  scenarios: {
    constant_load: {
      executor: "constant-vus",
      vus: 5,
      duration: DURATION,
    },
  },
};

const KNOWN_IDS = [1, 2, 3, 4, 5];

export default function () {
  const roll = Math.random();

  if (roll < 0.7) {
    const res = http.get(`${BASE_URL}/products`, {
      timeout: TIMEOUT,
      tags: { endpoint: "list" },
    });
    check(res, { "list status is not 5xx": (r) => r.status < 500 });
  } else if (roll < 0.9) {
    const id = KNOWN_IDS[Math.floor(Math.random() * KNOWN_IDS.length)];
    const res = http.get(`${BASE_URL}/products/${id}`, {
      timeout: TIMEOUT,
      tags: { endpoint: "get" },
    });
    check(res, { "get status is not 5xx": (r) => r.status < 500 });
  } else {
    const payload = JSON.stringify({
      name: `product-${__VU}-${__ITER}`,
      price: Math.round(Math.random() * 10000) / 100,
      stock: Math.floor(Math.random() * 100),
    });
    const res = http.post(`${BASE_URL}/products`, payload, {
      timeout: TIMEOUT,
      headers: { "Content-Type": "application/json" },
      tags: { endpoint: "create" },
    });
    check(res, { "create status is not 5xx": (r) => r.status < 500 });
  }

  sleep(0.5);
}
