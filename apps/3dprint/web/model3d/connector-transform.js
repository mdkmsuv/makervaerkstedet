export function isConnector(node) { return node?.generator?.kind === 'connector'; }

// Check the effective world axes. An orthogonal reflection is also rigid.
export function hasRigidAxes(matrix, tolerance = 1e-8) {
  const dot = (a, b) => matrix[a] * matrix[b] + matrix[a + 1] * matrix[b + 1] + matrix[a + 2] * matrix[b + 2];
  return [dot(0, 0) - 1, dot(4, 4) - 1, dot(8, 8) - 1, dot(0, 4), dot(0, 8), dot(4, 8)]
    .every(value => Math.abs(value) <= tolerance);
}
