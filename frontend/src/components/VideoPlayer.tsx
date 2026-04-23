import FrameCanvas from './FrameCanvas'
import PlayerControls from './PlayerControls'
import Toolbox from './Toolbox'

export default function VideoPlayer() {
  return (
    <div className="video-player">
      <FrameCanvas />
      <Toolbox />
      <PlayerControls />
    </div>
  )
}
