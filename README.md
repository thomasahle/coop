For ordering food at https://mad.coop.dk/

Allows storing lists of products usually picked, as well as alternatives when a product is sold out.

Examples:

    Show full list of options
        python3 coop.py --help
        
    See a list of your latest orders
        python3 coop.py ordrer
        
    Write an earlier order to a file for easy re-ordering.
        python3 coop.py ordrer N --write FILNAVN
        
    Pick a time for delivery
        python3 coop.py tidspunkt --pick --day 0 --hour 18     
        
    Read a csv file and add the products to the basket
        python3 coop.py kurv --read FILNAVN
        
    See the current basket
        python3 coop.py kurv
        
    Delete the current basket
        python3 coop.py kurv --clear
