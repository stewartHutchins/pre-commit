import sbt.Command

object SleepCommand {
  def sleepCommand = Command.single("sleep") { (state, arg) =>
    val waitSec = Integer.parseInt(arg)
    Thread.sleep(waitSec * 1000L)
    state
  }
}
