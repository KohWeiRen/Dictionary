"""Authored vocabulary for useful, less familiar everyday expression.

Frequency alone cannot distinguish a useful word from a specialist term.
The definitions and examples here are original teaching text; dictionary
lookups only enrich the selected word with pronunciation and audio.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class EnglishWord:
    word: str
    meaning: str
    pattern: str
    examples: tuple[str, str]
    register: str = "neutral"


def _word(word, meaning, pattern, first, second, register="neutral"):
    return EnglishWord(word, meaning, pattern, (first, second), register)


WORDS = (
    _word("reticent", "Reluctant to share your thoughts or feelings.", "reticent about something", "She was reticent about why she left her job.", "He's usually reticent in large meetings."),
    _word("tenuous", "Weak, uncertain, or poorly supported.", "a tenuous connection / argument", "The connection between those two events is tenuous.", "We have only a tenuous agreement at this stage."),
    _word("equivocal", "Unclear or open to more than one interpretation.", "an equivocal answer / response", "His equivocal answer left us unsure whether he agreed.", "The test results were equivocal, so we need more evidence.", "formal"),
    _word("laconic", "Using very few words.", "a laconic reply", "Her laconic reply was simply, 'Maybe.'", "He's laconic, but he always makes his point.", "slightly formal"),
    _word("convoluted", "Unnecessarily complicated and hard to follow.", "a convoluted explanation / process", "The instructions are too convoluted to follow.", "There must be a simpler way through this convoluted process."),
    _word("circumspect", "Careful to consider risks before acting or speaking.", "circumspect about something", "I'm circumspect about sharing personal details online.", "We should be circumspect before making promises.", "slightly formal"),
    _word("cursory", "Quick and without much attention to detail.", "a cursory glance / check", "I gave the report a cursory glance before the meeting.", "A cursory check won't catch every mistake."),
    _word("discerning", "Good at noticing differences and judging quality.", "a discerning reader / customer", "She's a discerning reader who notices weak arguments.", "Discerning customers will notice the improvement."),
    _word("exacting", "Demanding a high standard of care or accuracy.", "exacting standards", "Our client has exacting standards.", "Editing this document is exacting work."),
    _word("forthright", "Direct and honest when expressing your views.", "forthright about something", "She was forthright about what needed to change.", "I appreciate your forthright feedback."),
    _word("inadvertent", "Unintentional; done without realizing it.", "an inadvertent mistake / omission", "Leaving your name off the list was an inadvertent mistake.", "The email contained an inadvertent disclosure of the price."),
    _word("incongruous", "Out of place or inconsistent with the surroundings.", "incongruous with / an incongruous detail", "His cheerful tone was incongruous with the bad news.", "The plastic chairs looked incongruous in the elegant room."),
    _word("ostensible", "Stated or apparent, though possibly not the real reason.", "the ostensible reason / purpose", "The ostensible reason for the meeting was to discuss costs.", "Her ostensible aim was to help, but she mostly wanted attention.", "formal"),
    _word("perfunctory", "Done with little interest or effort, just to fulfil a duty.", "a perfunctory apology / check", "He offered a perfunctory apology and changed the subject.", "The inspection felt perfunctory rather than thorough."),
    _word("salient", "Especially noticeable or relevant to the discussion.", "the salient point / feature", "Let me explain the salient points before we decide.", "The most salient difference is the price.", "slightly formal"),
    _word("tacit", "Understood or agreed without being stated directly.", "tacit agreement / approval", "We had a tacit agreement to split the bill.", "Her silence was taken as tacit approval."),
    _word("amenable", "Willing to consider or accept a suggestion.", "amenable to something", "I'm amenable to changing the meeting time.", "Would your team be amenable to a shorter trial?"),
    _word("commensurate", "Appropriate in size or amount compared with something else.", "commensurate with something", "The pay should be commensurate with the responsibility.", "The results weren't commensurate with the effort we put in.", "formal"),
    _word("contingent", "Dependent on something else happening.", "contingent on something", "Our trip is contingent on getting leave approved.", "The offer is contingent on a successful inspection."),
    _word("disparate", "Very different from one another.", "disparate views / sources", "We need to bring these disparate ideas into one plan.", "The information came from several disparate sources."),
    _word("expedient", "Convenient for an immediate purpose, sometimes at a cost to principle.", "an expedient solution", "It was expedient to postpone the decision until Friday.", "The cheapest fix may be expedient, but it won't last."),
    _word("incisive", "Clear and sharp in identifying what matters.", "an incisive question / analysis", "She asked an incisive question that exposed the problem.", "His incisive feedback helped me rewrite the proposal."),
    _word("judicious", "Showing careful and sensible judgment.", "judicious use of something", "A judicious use of examples will make your point clearer.", "We need to be judicious about how we spend the budget."),
    _word("measured", "Calm and carefully considered.", "a measured response / tone", "She gave a measured response to the criticism.", "Let's take a measured approach rather than rushing."),
    _word("oblique", "Indirect rather than clearly stated.", "an oblique reference to something", "He made an oblique reference to the earlier disagreement.", "Her comment was an oblique way of asking us to leave."),
    _word("palatable", "Acceptable or agreeable, even if not ideal.", "make something more palatable", "A discount would make the price more palatable.", "They changed the wording to make the proposal palatable."),
    _word("pertinent", "Directly relevant to the matter being discussed.", "pertinent to something", "That question is pertinent to our decision.", "Please include only the pertinent details."),
    _word("plausible", "Seeming reasonable or believable.", "a plausible explanation", "That's a plausible explanation for the delay.", "We need a plausible plan, not just a hopeful estimate."),
    _word("recalcitrant", "Stubbornly unwilling to cooperate or follow instructions.", "a recalcitrant person / group", "The manager struggled to persuade a recalcitrant team member.", "A few recalcitrant residents refused to move their cars.", "formal; can sound critical"),
    _word("scrupulous", "Extremely careful to be accurate, fair, or honest.", "scrupulous about something", "She's scrupulous about checking every expense.", "We need to be scrupulous in how we report the results."),
    _word("succinct", "Brief while still expressing the important points clearly.", "a succinct summary", "Could you give me a succinct summary of the issue?", "Her email was succinct and easy to act on."),
    _word("superfluous", "More than is needed; unnecessary.", "superfluous details / words", "Remove the superfluous details from the introduction.", "A second approval step seems superfluous here."),
    _word("tractable", "Easy to manage, influence, or solve.", "a tractable problem", "Breaking the task into smaller pieces makes it more tractable.", "The scheduling problem is tractable if everyone is flexible.", "slightly formal"),
    _word("unwieldy", "Awkward to handle because of size or complexity.", "an unwieldy system / object", "This spreadsheet has become too unwieldy to maintain.", "The suitcase was unwieldy on the crowded train."),
    _word("vacillate", "Keep changing between different opinions or choices.", "vacillate between two options", "I keep vacillating between the two apartments.", "We can't vacillate forever; we need a decision today.", "slightly formal"),
    _word("wary", "Cautious because something might be risky or misleading.", "wary of something", "I'm wary of deals that sound too good to be true.", "She's wary of committing before she sees the contract."),
    _word("alacrity", "Cheerful eagerness to do something.", "with alacrity", "She accepted the invitation with alacrity.", "He tackled the new assignment with surprising alacrity.", "formal; often playful in conversation"),
    _word("ambivalent", "Having mixed or conflicting feelings about something.", "ambivalent about something", "I'm ambivalent about moving: excited, but also reluctant.", "She feels ambivalent about accepting the promotion."),
    _word("assiduous", "Showing persistent care and effort.", "assiduous in doing something", "She's assiduous in following up with clients.", "His assiduous preparation paid off in the interview.", "formal"),
    _word("cogent", "Clear, logical, and convincing.", "a cogent argument", "He made a cogent argument for changing the schedule.", "I need a cogent reason to approve the extra cost."),
    _word("conciliatory", "Intended to reduce tension or end a disagreement.", "a conciliatory tone / gesture", "She sent a conciliatory message after the argument.", "His conciliatory tone helped us restart the discussion."),
    _word("deleterious", "Causing harm or damage.", "a deleterious effect on something", "Constant interruptions have a deleterious effect on my work.", "The change could be deleterious to staff morale.", "formal"),
    _word("diffident", "Hesitant because of a lack of confidence.", "a diffident manner", "He sounded diffident when he offered his suggestion.", "She's diffident in meetings, despite knowing the subject well."),
    _word("disingenuous", "Not fully honest, often by pretending to know less than you do.", "a disingenuous claim / response", "It's disingenuous to say nobody warned us.", "Her surprised reaction seemed disingenuous."),
    _word("ebullient", "Full of enthusiasm and cheerful energy.", "an ebullient mood / personality", "She was ebullient after hearing the good news.", "His ebullient personality lifts the mood of the whole team."),
    _word("fastidious", "Very particular about details, cleanliness, or accuracy.", "fastidious about something", "He's fastidious about keeping his desk tidy.", "She's a fastidious editor who catches tiny inconsistencies."),
    _word("intransigent", "Unwilling to change your position or compromise.", "an intransigent stance", "Their intransigent stance has stalled the negotiations.", "We won't solve this if both sides remain intransigent."),
    _word("magnanimous", "Generous and forgiving, especially toward a rival.", "magnanimous in victory / defeat", "She was magnanimous in victory and praised her opponent.", "It was magnanimous of him to forgive the mistake."),
    _word("noncommittal", "Avoiding a definite opinion, promise, or decision.", "a noncommittal answer", "He gave a noncommittal answer about joining us.", "She remained noncommittal until she had more information."),
    _word("opportune", "Happening at a particularly useful or suitable time.", "an opportune moment", "This is an opportune moment to ask for feedback.", "Your call came at an opportune time."),
    _word("prescient", "Showing an ability to anticipate what will happen.", "a prescient warning / observation", "Her warning about rising costs turned out to be prescient.", "His prescient decision saved us a lot of trouble."),
    _word("prosaic", "Ordinary and lacking excitement or imagination.", "a prosaic explanation", "The explanation was prosaic: the battery was dead.", "Most of the work is prosaic, but it still matters."),
    _word("sanguine", "Confident and optimistic, even when there are difficulties.", "sanguine about something", "She's sanguine about the project's chances.", "I'm less sanguine about finishing before Friday.", "slightly formal"),
    _word("tangential", "Only loosely connected to the main subject.", "tangential to something", "That issue is tangential to today's discussion.", "Let's avoid tangential details and focus on the decision."),
    _word("unequivocal", "Completely clear and leaving no room for doubt.", "an unequivocal answer / commitment", "We need an unequivocal answer by tomorrow.", "Her support for the proposal was unequivocal."),
    _word("veritable", "Used to emphasize how fully a description applies.", "a veritable source / feast / mountain", "My inbox is a veritable mountain of unread messages.", "She's a veritable fountain of useful advice.", "emphatic; sometimes playful"),
)
