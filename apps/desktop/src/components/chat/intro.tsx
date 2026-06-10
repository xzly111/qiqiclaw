export type IntroProps = {
  personality?: string
  seed?: number
}

const TAGLINE = '欢迎使用，属于您的专属智能体。'

export function Intro(_props: IntroProps) {
  return (
    <div
      className="pointer-events-none flex w-full min-w-0 flex-col items-center justify-center px-3 py-6 text-center text-muted-foreground sm:px-6 lg:px-8"
      data-slot="aui_intro"
    >
      <div className="w-full min-w-0">
        <div aria-label="QIQI-Claw" className="qiqiclaw-intro-wordmark mx-auto mb-4 select-none">
          QIQI-Claw
        </div>

        <p className="m-0 text-center text-sm leading-normal text-muted-foreground sm:text-base">{TAGLINE}</p>
      </div>
    </div>
  )
}
