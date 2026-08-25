package org.twak.viewTrace.franken.style;

import java.awt.BorderLayout;
import java.awt.image.BufferedImage;
import java.io.File;
import java.io.IOException;
import java.util.Arrays;
import java.util.Random;

import javax.imageio.ImageIO;
import javax.swing.JLabel;
import javax.swing.JPanel;
import javax.swing.JSlider;
import javax.swing.event.ChangeEvent;
import javax.swing.event.ChangeListener;

import org.twak.utils.ui.ListDownLayout;
import org.twak.viewTrace.franken.App;
import org.twak.viewTrace.franken.NetInfo;
import org.twak.viewTrace.franken.SelectedApps;
import org.twak.viewTrace.franken.style.ui.UIVector;
import org.twak.viewTrace.franken.style.ui.UIVector.MeanImageProvider;
import org.twak.viewTrace.franken.style.ui.UIVector.ReferenceVectorProvider;

public class GaussStyle implements StyleSource, MeanImageProvider, ReferenceVectorProvider {

	public double[] mean;
	double std;
	transient BufferedImage meanImage;
	public double[] referenceFallbackMean;
	public boolean meanFromReference;
	Class target;

	public GaussStyle( Class target ) {
		this.mean = new double[ NetInfo.get(target).sizeZ];
		this.std = 0;
		this.target = target;
	}

	
	@Override
	public StyleSource copy() {
		GaussStyle out = new GaussStyle( target );
		out.mean = Arrays.copyOf( mean, mean.length );
		out.std = std;
		out.meanImage = meanImage;
		out.referenceFallbackMean = referenceFallbackMean == null ? null
				: Arrays.copyOf( referenceFallbackMean, referenceFallbackMean.length );
		out.meanFromReference = meanFromReference;
		return out;
	}
	@Override
	public double[] draw( Random random, App app ) {

		double[] out = new double[mean.length];

		for ( int i = 0; i < out.length; i++ )
			out[ i ] = random.nextGaussian() * std + mean[ i ];

		return out;
	}

	@Override
	public JPanel getUI( Runnable update, SelectedApps sa ) {
		return getUI( update, sa, null );
	}

	public JPanel getUI( Runnable update, SelectedApps sa, File defaultReference ) {

		JPanel out = new JPanel( new ListDownLayout() );
		
		JPanel line = new JPanel( new BorderLayout() );

		line.add( new JLabel( "σ:" ), BorderLayout.WEST );

		JSlider deviation = new JSlider( 0, 1000, (int) ( std * 500 ) );

		line.add( deviation, BorderLayout.CENTER );

		out.add( line );
		deviation.addChangeListener( new ChangeListener() {
			@Override
			public void stateChanged( ChangeEvent e ) {
				if ( !deviation.getValueIsAdjusting() ) {
					std = deviation.getValue() / 500.;
					update.run();
				}
			}
		} );

		out.add( new UIVector( mean, this, target, false, update, defaultReference ) );

		return out;
	}

	public boolean install( SelectedApps next ) {
		return false;
	}

	@Override
	public BufferedImage getMeanImage() {
		return meanImage;
	}

	@Override
	public void setMeanImage( File f ) {
		meanImage = null;
		if ( f != null )
			try {
				meanImage = ImageIO.read( f );
			} catch ( IOException e ) {
				e.printStackTrace();
			}
	}

	@Override
	public synchronized void applyReferenceVector( double[] encoded, BufferedImage preview ) {
		if ( !meanFromReference )
			referenceFallbackMean = Arrays.copyOf( mean, mean.length );
		ReferenceStyleSupport.commitLatent( mean, encoded );
		meanImage = preview;
		meanFromReference = true;
	}

	@Override
	public synchronized void clearReferenceVector() {
		if ( meanFromReference && referenceFallbackMean != null
				&& referenceFallbackMean.length == mean.length )
			System.arraycopy( referenceFallbackMean, 0, mean, 0, mean.length );
		meanImage = null;
		referenceFallbackMean = null;
		meanFromReference = false;
	}

	@Override
	public boolean hasReferenceVector() {
		return meanFromReference;
	}
	
	@Override
	public void install( App app ) {
		app.styleSource = new GaussStyle( app.getClass() );
	}
	
	@Override
	public String toString() {
		String out = "Gauss [";
		for (double d : mean)
			out += ", "+d;
		return out +"]";
	}
}
