export const liters = (n: number) => Math.round(n).toLocaleString() + ' L'
export const title = (id: string) => id.replace(/^(station|depot|route|region)-/, '').replace(/[-_]/g, ' ')
