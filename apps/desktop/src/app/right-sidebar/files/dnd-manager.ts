import { createDragDropManager } from 'dnd-core'
import { HTML5Backend } from 'react-dnd-html5-backend'

let sharedManager: ReturnType<typeof createDragDropManager> | null = null

export function getSharedTreeDndManager() {
  if (!sharedManager) {
    sharedManager = createDragDropManager(HTML5Backend)
  }

  return sharedManager
}
