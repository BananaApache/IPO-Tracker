"""Words that are ordinary English before they are company names.

Bundled in code rather than read from /usr/share/dict/words: the slim Python
image has no system dictionary, and a matcher whose behaviour depends on which
host it runs on is not reproducible.

Curated rather than exhaustive. A full dictionary is the wrong tool -- it
contains "electra" and "amaero"-shaped rarities that are perfectly good brand
tokens. What matters is the high-frequency words a reader would never read as a
company name without context.
"""

COMMON_WORDS = frozenset("""
a about above across after again against all almost alone along already also
always among an and another any anyone anything are around as at away back bad
be because been before begin behind being below best better between big both
but by call can case change check circle clear clearly close come common
company could country cover create current data day deal deep design detail
different do down draw drive early easy edge end enough even ever every
example face fact fall family far fast few field figure file find first five
follow for force form forward found four free from front full future game
general get give go good great green group grow half hand happen hard have he
head hear help her here high him his hold home hope house how however idea if
in include increase inside into is issue it its just keep key kind know large
last late later lead learn leave left less let level life light like line list
little live local long look lose lot love low main major make man many mark
market match may maybe me mean meet member might mind minute miss model money
month more most move much must my name near need never new next nice night no
none nor not note nothing now number of off offer office often oil ok old on
once one only open or order other our out over own page paper part past pay
people perhaps person pick place plan play point policy poor position possible
power present press price problem process program project provide public pull
push put quality question quick quite race raise range rate rather reach read
ready real reason receive recent record red reduce report result return rich
right rise risk road rock role room round rule run safe same save say scale
school science score sea season second section see seem sell send sense series
serious serve service set several shape share she short should show side sign
similar simple since single sir site situation six size skill small so social
society some soon sort sound source south space speak special specific speed
spend sport spring staff stage stand standard star start state stay step still
stock stop store story strategy street strong structure student study style
subject success such sudden suffer suggest summer support sure surface system
table take talk task tax teach team tell ten term test than that the their
them then there these they thing think third this those though thought three
through throw thus time to today together too top total touch toward town
trade train travel treat tree trip true try turn two type under understand
union unit until up upon use user usual value various very view visit voice
vote wait walk wall want war watch water wave way we week weight well west
what when where whether which while white who whole why wide will win wind
window wish with within without word work world would write year yes yet you
young your
""".split())
